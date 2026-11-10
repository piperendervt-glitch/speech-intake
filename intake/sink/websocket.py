"""発話レコードを WebSocket で配信する（ws://127.0.0.1:8766/ws）。localhost のみで待ち受ける。

送るメッセージ（JSON 1 つ = 1 メッセージ）:
  {"type": "hello",     "info": {...run.json と同じ...}}          接続直後と、新しい run の開始時
  {"type": "speech_start", "seg", "t", "t_detect", "t_unix", "p"}  VAD が発話の始まりを確定した時点（STT を待たない。段10）
  {"type": "speech_end",   "seg", "t0", "t1", "t_detect", "t_unix"}  VAD が区間を閉じた時点（STT を待たない）
  {"type": "speech_dropped", "seg", "reason", "t_unix"}           区間から発話レコードが出ないと決まったとき（vad_short / stt_empty）
  {"type": "utterance", "record": {...発話レコード...}, "seg", "timing": {"vad_close_unix", "stt_done_unix"}}
                                                                   発話レコードが 1 つ確定するたび（seg / timing は段10 で追加）
  {"type": "revise",    "records": [...発話レコードの全列...]}     話者の付け直しなどで列全体が変わったとき（同じ id を置き換える）
  {"type": "trace",     "stage": "vad" | "speaker", "row": {...}}  段ごとの記録（購読時に ?trace=1 を付けたときだけ）
  {"type": "end",       "stats": {...}}                            入力の終わり

途中から接続しても、その run の hello と、それまでの発話レコードが先に届く（取りこぼさない）。
speech_start / speech_end / speech_dropped は再送しない（その時点で接続していた購読者にだけ届く）。
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

from aiohttp import WSMsgType, web

from ..audio import AudioChunk
from ..config import LOCAL_HOSTS
from ..schema import Utterance


class WebSocketSink:
    def __init__(self, host: str = "127.0.0.1", port: int = 8766, on_message=None):
        if host not in LOCAL_HOSTS:
            raise ValueError(f"WebSocket は localhost 以外で待ち受けない: {host!r}")
        self.host, self.port = host, port
        self.on_message = on_message  # 購読側から届いたメッセージ（wearer 区間など）を渡す先
        self._clients: dict[web.WebSocketResponse, bool] = {}  # ws -> trace を送るか
        self._hello: dict[str, Any] | None = None
        self._backlog: list[dict[str, Any]] = []
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._serve, name="intake-ws", daemon=True)
        self._thread.start()
        if not self._ready.wait(10):
            raise RuntimeError("WebSocket サーバが起動しない")
        if self._error:
            raise self._error

    # ---- サーバ（別スレッドのイベントループ） ----
    def _serve(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._error = None
        app = web.Application()
        app.router.add_get("/ws", self._handler)
        self._runner = web.AppRunner(app)
        try:
            self._loop.run_until_complete(self._runner.setup())
            site = web.TCPSite(self._runner, self.host, self.port)
            self._loop.run_until_complete(site.start())
        except OSError as e:
            self._error = e
            self._ready.set()
            return
        self._ready.set()
        self._loop.run_forever()

    async def _handler(self, req: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(req)
        self._clients[ws] = req.query.get("trace") == "1"
        if self._hello:
            await ws.send_str(json.dumps(self._hello, ensure_ascii=False))
        for m in list(self._backlog):
            await ws.send_str(json.dumps(m, ensure_ascii=False))
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT and self.on_message:
                    try:
                        self.on_message(json.loads(msg.data))
                    except Exception as e:  # noqa: BLE001
                        await ws.send_str(json.dumps({"type": "error", "error": str(e)}, ensure_ascii=False))
        finally:
            self._clients.pop(ws, None)
        return ws

    async def _broadcast(self, m: dict[str, Any], trace: bool) -> None:
        data = json.dumps(m, ensure_ascii=False)
        for ws, want_trace in list(self._clients.items()):
            if trace and not want_trace:
                continue
            try:
                await ws.send_str(data)
            except ConnectionError:
                self._clients.pop(ws, None)

    def _send(self, m: dict[str, Any], trace: bool = False) -> None:
        asyncio.run_coroutine_threadsafe(self._broadcast(m, trace), self._loop).result(timeout=5)

    # ---- Sink ----
    def start(self, info: dict[str, Any]) -> None:
        self._hello = {"type": "hello", "info": info}
        self._backlog = []
        self._send(self._hello)

    def audio(self, chunk: AudioChunk) -> None:  # noqa: ARG002
        return None

    def trace(self, stage: str, obj: dict[str, Any]) -> None:
        if stage in ("vad_prob", "events"):  # 枠ごとの確率は量が多い／events は event() で配っている
            return
        self._send({"type": "trace", "stage": stage, "row": obj}, trace=True)

    def event(self, msg: dict[str, Any]) -> None:
        self._send(msg)  # backlog に入れない（途中から来た購読者には再送しない）

    def emit(self, utt: Utterance, seg: int | None = None, timing: dict[str, float] | None = None) -> None:
        m: dict[str, Any] = {"type": "utterance", "record": utt.to_dict()}
        if seg is not None:
            m["seg"] = seg
        if timing:
            m["timing"] = timing
        self._backlog.append(m)
        self._send(m)

    def revise(self, utts: list[Utterance]) -> None:
        m = {"type": "revise", "records": [u.to_dict() for u in utts]}
        self._backlog = [m]
        self._send(m)

    def end(self, stats: dict[str, Any]) -> None:
        m = {"type": "end", "stats": stats}
        self._backlog.append(m)
        self._send(m)

    def close(self) -> None:
        pass  # 配信は shutdown() まで続ける（record/replay が終わってもすぐには切らない）

    def shutdown(self) -> None:
        async def stop():
            for ws in list(self._clients):
                await ws.close()
            await self._runner.cleanup()

        asyncio.run_coroutine_threadsafe(stop(), self._loop).result(timeout=5)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
