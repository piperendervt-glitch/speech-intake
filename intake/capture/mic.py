from __future__ import annotations

import queue
import sys
import threading
import time
from datetime import datetime
from typing import Any, Iterator

import numpy as np
import soundfile as sf

from ..audio import AudioChunk
from ..resample import StreamResampler
from ..session import SessionStore


def _sd():
    try:
        import sounddevice as sd  # noqa: PLC0415
    except OSError as e:  # PortAudio が無い環境
        raise RuntimeError(f"sounddevice を読み込めない（PortAudio が必要）: {e}") from e
    return sd


_BT_WORDS = ("hands-free", "handsfree", "hands free", "ハンズフリー", "headset", "ヘッドセット", "airpods", "bluetooth", "bt ")


def _is_bt(name: str) -> bool:
    n = name.lower()
    return any(w in n for w in _BT_WORDS)


def list_input_devices(probe_rate: int | None = 16000) -> list[dict[str, Any]]:
    """入力に使える機器の一覧（段11 で項目を増やした）。

    index / name / hostapi（MME・WASAPI など）/ channels / default_samplerate / bt（名前から Bluetooth らしい）/
    open（probe_rate で開けるか："ok" / "native"＝既定のレートでなら開ける / "ng"）。
    open は PortAudio の check_input_settings で確かめるだけで、録音はしない。
    """
    sd = _sd()
    apis = [a["name"] for a in sd.query_hostapis()]
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] <= 0:
            continue
        row = {"index": i, "name": d["name"], "hostapi": apis[d["hostapi"]] if d["hostapi"] < len(apis) else "?",
               "channels": int(d["max_input_channels"]), "default_samplerate": float(d["default_samplerate"]),
               "bt": _is_bt(d["name"])}
        if probe_rate:
            row["open"] = _probe(sd, i, probe_rate, int(d["default_samplerate"]))
        out.append(row)
    return out


def _probe(sd, index: int, rate: int, native: int) -> str:
    try:
        sd.check_input_settings(device=index, samplerate=rate, channels=1, dtype="float32")
        return "ok"
    except Exception:  # noqa: BLE001
        pass
    try:
        sd.check_input_settings(device=index, samplerate=native, channels=1, dtype="float32")
        return "native"
    except Exception:  # noqa: BLE001
        return "ng"


def resolve_device(spec: int | str | None, devices: list[dict[str, Any]], hostapi: str | None = None) -> int | None:
    """capture.device（番号・名前の一部）を機器の番号にする。

    "AirPods@WASAPI" のように @ の後ろでホスト API を絞れる（capture.hostapi と同じ）。大文字小文字は区別しない。
    名前が完全に一致する行があればそれを選ぶ。複数当たったら候補を並べて止まる（ValueError）。
    """
    if spec is None or spec == "":
        return None
    if isinstance(spec, int) or (isinstance(spec, str) and spec.strip().isdigit()):
        i = int(spec)
        if devices and i not in {d["index"] for d in devices}:
            raise ValueError(f"入力機器の番号 {i} は無い（python -m intake devices で確かめる）")
        return i
    name = str(spec).strip()
    if "@" in name:
        name, _, api = name.rpartition("@")
        name, hostapi = name.strip(), api.strip()
    cand = [d for d in devices if name.lower() in d["name"].lower()]
    if hostapi:
        cand = [d for d in cand if hostapi.lower() in d["hostapi"].lower()]
    exact = [d for d in cand if d["name"].lower() == name.lower()]
    if len(exact) == 1:
        cand = exact
    if not cand:
        raise ValueError(f"名前に {name!r} を含む入力機器が無い" + (f"（ホスト API {hostapi!r}）" if hostapi else "")
                         + "。python -m intake devices で確かめる")
    if len(cand) > 1:
        lines = "\n".join(f"  {d['index']}\t{d['hostapi']}\t{d['name']}" for d in cand)
        raise ValueError(f"{spec!r} に当たる入力機器が {len(cand)} つある。番号か「名前@ホストAPI」（例 \"{name}@MME\"）で 1 つに絞る：\n{lines}")
    return cand[0]["index"]


class MicCapture:
    """マイクから録音し、sessions/<sid>/audio.wav に書きつつチャンクを返す。

    時刻は「書いたサンプル数 / sample_rate」だけで決める（壁時計は使わない）。
    stream_factory はテスト用の差し込み口（sounddevice.InputStream と同じ引数を受ける）。
    block_ms     1 回に受け取る音声の長さ。短いほど VAD に早く届く（段10。VAD の枠は 32 ms なので、それより短くしても
                 確定が早まるのは枠の切れ目との差の分だけ）
    latency      PortAudio に頼む入力の遅れ（"low" / "high" / 秒。None は sounddevice の既定 = "high"）
    device_rate  機器を開くレート（段11）。None = まず sample_rate で開き、断られたら機器の既定のレートで開く。
                 "native" = 機器の既定のレート。数 = そのレート。sample_rate と違えば 16 kHz に直して保存する
    stall_s      録音中にこの秒数ブロックが届かなければ、機器が消えたとみなして止める（それまでは保存済み）
    """

    def __init__(
        self,
        store: SessionStore,
        session: str,
        sample_rate: int = 16000,
        device: int | str | None = None,
        block_ms: int = 32,
        latency: str | float | None = None,
        stream_factory=None,
        flush_every_s: float = 1.0,
        device_rate: int | str | None = None,
        stall_s: float = 2.0,
        first_block_s: float = 8.0,
        source: str = "mic",
        extra_meta: dict[str, Any] | None = None,
    ):
        self.store = store
        self.session = session
        self.sample_rate = sample_rate
        self.device = device
        self.block_ms = block_ms
        self.latency = latency
        self.device_rate = device_rate
        self.stall_s = stall_s
        self.first_block_s = first_block_s
        self.source = source
        self.extra_meta = extra_meta or {}
        self._stream_factory = stream_factory
        self._q: queue.Queue[np.ndarray | None] = queue.Queue()
        self._stop = threading.Event()
        self._flush_every = flush_every_s
        self.overflows = 0
        self.samples_written = 0
        self.t0_unix: float | None = None
        self._prepared = False
        self._rate = sample_rate  # 実際に開いたレート
        self.max_samples: int | None = None  # これだけ録ったら止める（record --seconds）
        self.lost: str | None = None  # 機器が消えたときの説明（段11）

    @property
    def blocksize(self) -> int:
        return int(self._rate * self.block_ms / 1000)

    def stop(self) -> None:
        self._stop.set()

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ARG002
        if status and getattr(status, "input_overflow", False):
            self.overflows += 1
        if self.t0_unix is None:
            # 最初のブロックの先頭サンプルの壁時計（概算。外部プロセスの時刻をセッション秒に直す手がかり）
            self.t0_unix = time.time() - frames / self._rate
        self._q.put(np.array(indata[:, 0], dtype=np.float32, copy=True))

    def device_info(self) -> dict[str, Any]:
        """開く機器の名前・ホスト API・既定のレート（sounddevice が無い・テストの差し込みのときは空）。"""
        if self._stream_factory is not None:
            return {}
        try:
            sd = _sd()
            d = sd.query_devices(self.device, "input")
            apis = sd.query_hostapis()
            return {"device_name": d["name"], "hostapi": apis[d["hostapi"]]["name"],
                    "device_default_rate": float(d["default_samplerate"]), "device_channels": int(d["max_input_channels"])}
        except Exception:  # noqa: BLE001
            return {}

    def prepare(self) -> None:
        """セッションを作る。run の出力（runs/<run>/）を置く前に呼ぶ（record はこの順。段10 で直した）。"""
        if self._prepared:
            return
        self._dev = self.device_info()
        self.store.create(
            self.session,
            {
                "source": self.source,
                "device": self.device,
                **self._dev,
                "sample_rate": self.sample_rate,
                **self.extra_meta,
                "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "status": "recording",
            },
        )
        self._prepared = True

    def _open(self, factory):
        def make(rate: int):
            self._rate = int(rate)
            return factory(
                samplerate=self._rate,
                channels=1,
                dtype="float32",
                blocksize=self.blocksize,
                device=self.device,
                callback=self._callback,
                **({"latency": self.latency} if self.latency is not None else {}),
            )

        native = int(self._dev.get("device_default_rate") or self.sample_rate)
        if self.device_rate == "native":
            return make(native)
        if isinstance(self.device_rate, (int, float)) or (isinstance(self.device_rate, str) and self.device_rate.isdigit()):
            return make(int(self.device_rate))
        try:
            return make(self.sample_rate)
        except Exception as e:  # noqa: BLE001  PortAudioError（Invalid sample rate）など
            if native == self.sample_rate:
                raise
            print(f"[intake] {self.sample_rate} Hz で開けない（{e}）。機器の既定 {native} Hz で開いて 16 kHz に直す", file=sys.stderr)
            return make(native)

    def chunks(self) -> Iterator[AudioChunk]:
        factory = self._stream_factory or _sd().InputStream
        self.prepare()
        path = self.store.audio_path(self.session)
        last_flush = time.monotonic()
        rs: StreamResampler | None = None

        def out(block: np.ndarray, wav) -> AudioChunk | None:
            x = rs.process(block) if rs is not None else block
            if len(x) == 0:
                return None
            t0 = self.samples_written / self.sample_rate
            wav.write(x)
            self.samples_written += len(x)
            return AudioChunk(t0=t0, samples=x, sample_rate=self.sample_rate)

        with sf.SoundFile(path, "w", samplerate=self.sample_rate, channels=1, subtype="PCM_16") as wav:
            stream = self._open(factory)
            if self._rate != self.sample_rate:
                rs = StreamResampler(self._rate, self.sample_rate)
            self.store.update_meta(self.session, device_rate=self._rate, resampled=rs is not None)
            try:
                with stream:
                    lat = getattr(stream, "latency", None)  # PortAudio が見積もった入力の遅れ（秒）
                    if isinstance(lat, (int, float)):
                        self.store.update_meta(self.session, input_latency_s=round(float(lat), 4), block_ms=round(self.blocksize / self._rate * 1000, 1))
                    last_block = opened = time.monotonic()
                    got_any = False
                    while not self._stop.is_set():
                        try:
                            block = self._q.get(timeout=0.1)
                        except queue.Empty:
                            now = time.monotonic()
                            active = getattr(stream, "active", True)
                            if got_any and (now - last_block > self.stall_s or not active):
                                self.lost = f"録音中に機器から音が届かなくなった（{now - last_block:.1f} 秒）。電池切れ・接続切れ・既定の機器の切り替えを確かめる"
                                break
                            if not got_any and (now - opened > self.first_block_s or not active):
                                self.lost = f"機器を開いたが {now - opened:.1f} 秒たっても音が届かない。別の行（ホスト API）を試す"
                                break
                            continue
                        last_block = time.monotonic()
                        if not got_any:
                            got_any = True
                            if self.t0_unix is not None:
                                self.store.update_meta(self.session, t0_unix=round(self.t0_unix, 4))
                        c = out(block, wav)
                        if time.monotonic() - last_flush > self._flush_every:
                            wav.flush()  # ヘッダを更新し、録音中でも閲覧画面から読めるようにする
                            last_flush = time.monotonic()
                        if c is not None:
                            yield c
                        if self.max_samples is not None and self.samples_written >= self.max_samples:
                            self.stop()
                    # 止めた後に残ったブロックも保存する
                    while not self._q.empty():
                        block = self._q.get_nowait()
                        if block is None:
                            break
                        c = out(block, wav)
                        if c is not None:
                            yield c
            except Exception as e:  # noqa: BLE001  PortAudioError など（機器が消えて読めない）
                if self.samples_written == 0 and self.lost is None:
                    raise
                self.lost = f"録音中に機器の読み取りが失敗した（{type(e).__name__}: {e}）"
            finally:
                if rs is not None:
                    tail = rs.flush()
                    if len(tail):
                        wav.write(tail)
                        self.samples_written += len(tail)
                meta = {"status": "done", "duration": round(self.samples_written / self.sample_rate, 3), "overflows": self.overflows}
                if self.lost:
                    meta["ended_by"] = "device_lost"
                    meta["lost"] = self.lost
                    print(f"[intake] {self.lost}。それまでの {meta['duration']} 秒は保存した → {path}", file=sys.stderr)
                self.store.update_meta(self.session, **meta)
