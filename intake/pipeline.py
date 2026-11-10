"""段をつなぐ。入口（capture / replay）から AudioChunk を受け、vad → stt → phones → speaker → sink と流す。

各段は関数境界で独立に呼べる（Lumi Bench 向け）：
  vad.feed(chunk) / stt.transcribe(audio, t0, sr) / phones.recognize(audio, t0, sr) / speaker.assign(utts) / sink.emit(utt)

段10（ライブ会話向け）：VAD が区間の始まり・終わりを確定した時点で、STT を待たずに sink.event() で通知する
（speech_start / speech_end / speech_dropped）。pipeline.stt_thread: true なら STT 以降を別スレッドで回し、
STT の最中も VAD が止まらないようにする（次の speech_start が STT の終わりを待たない）。
通知と発話レコードの時刻の記録は trace/events.jsonl（閲覧画面で遅れを見る）。

段11（turn-bench からの要望）：
  - info（hello / run.json）に sessions_dir / session_dir の絶対パス
  - 暫定の文字 partial：区間が開いている間に、そこまでの音声を STT に通して sink.event() で配る（pipeline.partial）。
    発話レコードではない（utterances.jsonl には入らない。確定するのは従来どおり utterance）
  - 購読側からの {"type": "set", "vad": {"min_silence_ms": 200}}：動かしたまま VAD の設定を変え、config の通知で知らせる
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from . import __version__, registry
from .audio import AudioChunk
from .phones.base import assign_phones
from .schema import SCHEMA_VERSION, Utterance, make_utterance_id


class AudioBuffer:
    """直近 keep_s 秒の音声を保持し、[t0, t1) を切り出す。"""

    def __init__(self, sample_rate: int, keep_s: float = 600.0):
        self.sr = sample_rate
        self.keep = int(keep_s * sample_rate)
        self._chunks: deque[np.ndarray] = deque()
        self._start = 0  # 保持している先頭のサンプル番号
        self._len = 0

    def push(self, chunk: AudioChunk) -> None:
        self._chunks.append(chunk.samples)
        self._len += len(chunk.samples)
        while self._len - len(self._chunks[0]) > self.keep:
            n = len(self._chunks.popleft())
            self._len -= n
            self._start += n

    def slice(self, t0: float, t1: float) -> np.ndarray:
        a = max(int(round(t0 * self.sr)), self._start)
        b = min(int(round(t1 * self.sr)), self._start + self._len)
        if b <= a:
            return np.zeros(0, dtype=np.float32)
        allx = np.concatenate(self._chunks) if len(self._chunks) > 1 else self._chunks[0]
        if len(self._chunks) > 1:  # 次回の concat を減らす
            self._chunks = deque([allx])
        return allx[a - self._start : b - self._start]


def _check_vad_param(k: str, v: Any) -> None:
    ok = {"min_silence_ms": lambda x: isinstance(x, (int, float)) and 32 <= x <= 5000,
          "start_frames": lambda x: isinstance(x, int) and 1 <= x <= 50}
    if k not in ok:
        raise ValueError(f"動かしたまま変えられるのは vad.min_silence_ms（32〜5000）/ vad.start_frames（1〜50）だけ: {k!r}")
    if isinstance(v, bool) or not ok[k](v):
        raise ValueError(f"vad.{k} の値が範囲の外: {v!r}（min_silence_ms は 32〜5000、start_frames は 1〜50 の整数）")


class Pipeline:
    def __init__(self, cfg: dict[str, Any], session: str, sink: Any, run_name: str | None = None):
        self.cfg = cfg
        self.session = session
        self.sink = sink
        self.run_name = run_name or cfg.get("run_name", "default")
        self.sr = int(cfg.get("sample_rate", 16000))
        self.vad = registry.create("vad", cfg)
        self.stt = registry.create("stt", cfg)
        extra = {"session_dir": cfg["_session_dir"]} if cfg.get("speaker", {}).get("provider") == "wearer" and "_session_dir" in cfg else {}
        self.speaker = registry.create("speaker", cfg, **extra)
        self.phones = registry.create("phones", cfg)  # STT の後に同じ区間で動く（GPU は STT 優先）
        self.buf = AudioBuffer(self.sr)
        self.utts: list[Utterance] = []
        self._n = 0
        self.stats = {"chunks": 0, "audio_s": 0.0, "vad_segments": 0, "utterances": 0}
        self._lock = threading.Lock()  # sink への書き込み（STT のスレッドと VAD のスレッドから来る）
        self._spk_lock = threading.Lock()  # speaker.feed（VAD 側）と speaker.assign（STT 側）
        self.stt_thread = bool((cfg.get("pipeline") or {}).get("stt_thread", False))
        self._q: queue.Queue | None = None
        self._worker: threading.Thread | None = None
        self._err: BaseException | None = None
        self.live = False  # 音声が実時間で届く（mic / replay:realtime）。通知の時刻を音声の時計に直して残す
        self._clock0: float | None = None  # セッション 0 秒に当たる壁時計（チャンクが届いた時刻 - チャンクの終わり、の最小）
        self._close_unix: dict[int, float] = {}
        pc = (cfg.get("pipeline") or {}).get("partial") or {}
        self.partial_every = float(pc.get("every_ms") or 0) / 1000
        self.partial_silence = None if pc.get("silence_ms") is None else float(pc["silence_ms"]) / 1000
        self.partial_min = float(pc.get("min_ms", 300)) / 1000
        self.partial_on = (self.partial_every > 0 or self.partial_silence is not None) and self.stt_thread
        self._p_state: dict[str, Any] | None = None  # 開いている区間について、暫定を最後に頼んだ時刻など
        self._jobs = 0  # STT のスレッドに頼んで、まだ終わっていない仕事の数
        self._jobs_lock = threading.Lock()
        self._inbox: list[dict[str, Any]] = []  # 購読側から届いた set（次のチャンクで反映する）

    def info(self, source: str) -> dict[str, Any]:
        public = {k: v for k, v in self.cfg.items() if not k.startswith("_")}
        root = Path(self.cfg.get("sessions_dir", "sessions")).resolve()
        return {
            "session": self.session,
            "run": self.run_name,
            # 絶対パス（段11）。別のフォルダから起動した利用側が録音を探せるように。config.sessions_dir は設定に書いたまま
            "sessions_dir": str(root),
            "session_dir": str(root / self.session),
            "source": source,
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "intake_version": __version__,
            "schema_version": SCHEMA_VERSION,
            "providers": {"vad": self.vad.name, "stt": self.stt.name, "speaker": self.speaker.name, "phones": self.phones.name},
            "config": public,
        }

    # ---- sink への出口（スレッドをまたぐので 1 本の鍵で順に書く） ----
    def _out(self, name: str, *a: Any, **kw: Any) -> None:
        with self._lock:
            getattr(self.sink, name)(*a, **kw)

    def _notify(self, ev: dict[str, Any], now: float | None = None) -> None:
        """speech_start / speech_end / speech_dropped を配る。trace/events.jsonl にも同じものを残す。"""
        now = time.time() if now is None else now
        msg = {**ev, "t_unix": round(now, 4)}
        row = dict(msg)
        if self.live and self._clock0 is not None:
            row["t_emit"] = round(now - self._clock0, 3)  # 通知を出した時刻を音声の時計（セッション秒）で
        self._out("event", msg)
        self._out("trace", "events", row)
        self.stats["events"] = self.stats.get("events", 0) + 1

    def _vad_events(self) -> None:
        for ev in getattr(self.vad, "drain_events", lambda: [])():
            now = time.time()
            if ev["type"] == "speech_end" and not ev.get("dropped"):
                self._close_unix[ev["seg"]] = now
            self._notify(ev, now)

    def _segment(self, seg) -> None:
        self.stats["vad_segments"] += 1
        self._out("trace", "vad", seg.to_dict())
        audio = self.buf.slice(seg.t0, seg.t1).copy()
        close = self._close_unix.pop(seg.seg, None) if seg.seg is not None else None
        job = (seg, audio, close if close is not None else time.time())
        if not self.stt_thread:
            self._transcribe(*job)
            return
        self._submit(("final", *job))

    def _submit(self, job: tuple) -> None:
        if self._worker is None:
            self._q = queue.Queue()
            self._worker = threading.Thread(target=self._work, name="intake-stt", daemon=True)
            self._worker.start()
        with self._jobs_lock:
            self._jobs += 1
        self._q.put(job)

    def _work(self) -> None:
        while True:
            job = self._q.get()
            if job is None:
                return
            try:
                if job[0] == "partial":
                    self._partial(*job[1:])
                else:
                    self._transcribe(*job[1:])
                self._drain((self.stt, self.phones))
            except BaseException as e:  # noqa: BLE001
                self._err = e
                return
            finally:
                with self._jobs_lock:
                    self._jobs -= 1

    # ---- 暫定の文字（段11） ----
    def _maybe_partial(self, now_t: float) -> None:
        """区間が開いていて、STT のスレッドが空いていれば、そこまでの音声の文字起こしを頼む。

        空いていないときは頼まない（暫定が確定の utterance を待たせる量を、多くて暫定 1 回分にとどめる）。
        """
        op = getattr(self.vad, "open_segment", lambda: None)()
        if op is None:
            self._p_state = None
            return
        seg, t0, silent_from = op
        st = self._p_state
        if st is None or st["seg"] != seg:
            st = self._p_state = {"seg": seg, "last": t0, "silence_done": False}
        if silent_from is None:
            st["silence_done"] = False
        reason = end = None
        if silent_from is not None:
            if self.partial_silence is not None and not st["silence_done"] and now_t - silent_from >= self.partial_silence:
                reason, end = "silence", silent_from
        elif self.partial_every > 0 and now_t - st["last"] >= self.partial_every:
            reason, end = "interval", now_t
        if reason is None or end - t0 < self.partial_min or self._jobs > 0:
            return
        st["last"] = now_t
        if reason == "silence":
            st["silence_done"] = True
        a = max(t0 - getattr(self.vad, "pad", 0.0), 0.0)
        self._submit(("partial", seg, a, end, self.buf.slice(a, end).copy(), reason, time.time()))

    def _partial(self, seg: int, t0: float, t1: float, audio: np.ndarray, reason: str, asked_unix: float) -> None:
        res = self.stt.transcribe(audio, t0, self.sr)
        if not res:
            return
        lang = res[0].lang
        text = ("" if (lang or "").startswith(("ja", "zh")) else " ").join(r.text for r in res)
        now = time.time()
        self._notify({"type": "partial", "seg": seg, "t0": round(t0, 3), "t1": round(t1, 3), "text": text, "lang": lang,
                      "reason": reason, "stt_ms": round((now - asked_unix) * 1000)}, now)
        self.stats["partials"] = self.stats.get("partials", 0) + 1

    # ---- 購読側からのメッセージ（WebSocket のスレッドから呼ばれる） ----
    def on_message(self, m: dict[str, Any]) -> None:
        """{"type": "set", "vad": {"min_silence_ms": 200}} は VAD の設定変更、それ以外は speaker provider に渡す。

        形の誤りはここで例外にする（送ってきた購読者に error が返る）。反映は次のチャンクで（VAD のスレッドで変える）。
        """
        if isinstance(m, dict) and m.get("type") == "set":
            vad = m.get("vad")
            if set(m) - {"type", "vad"} or not isinstance(vad, dict) or not vad:
                raise ValueError('set で変えられるのは vad だけ：{"type": "set", "vad": {"min_silence_ms": 200}}')
            if not hasattr(self.vad, "set_params"):
                raise ValueError(f"この VAD（{self.vad.name}）は動かしたままの変更に対応していない")
            for k, v in vad.items():  # 値の検査だけ先に（同じ値を入れ直して確かめる）
                _check_vad_param(k, v)
            self._inbox.append(dict(vad))
            return
        fn = getattr(self.speaker, "on_message", None)
        if fn:
            fn(m)

    def _apply_sets(self) -> None:
        while self._inbox:
            changed = self._inbox.pop(0)
            now = self.vad.set_params(**changed)
            self._notify({"type": "config", "vad": now, "changed": {"vad": changed}})

    def _transcribe(self, seg, audio: np.ndarray, close_unix: float) -> None:
        new: list[Utterance] = []
        for r in self.stt.transcribe(audio, seg.t0, self.sr):
            new.append(
                Utterance(
                    id=make_utterance_id(self._n),
                    session=self.session,
                    t0=r.t0,
                    t1=r.t1,
                    text=r.text,
                    lang=r.lang,
                    words=r.words,
                    vad=self.vad.name,
                    stt=self.stt.name,
                )
            )
            self._n += 1
        timing = {"vad_close_unix": round(close_unix, 4), "stt_done_unix": round(time.time(), 4)}
        if self.phones.name != "none":
            phs = self.phones.recognize(audio, seg.t0, self.sr)
            timing["phones_done_unix"] = round(time.time(), 4)
            self.stats["phones"] = self.stats.get("phones", 0) + len(phs)
            for ph in assign_phones(new, phs, self.phones.name):  # 発話が無い区間の音素は trace にだけ残す
                self._out("trace", "phones_orphan", {**ph.to_dict(), "model": self.phones.name})
        with self._spk_lock:
            self.speaker.assign(new)
        if not new and seg.seg is not None:  # STT が何も返さなかった（雑音・短すぎる）
            self._notify({"type": "speech_dropped", "seg": seg.seg, "reason": "stt_empty"})
        for u in new:
            now = time.time()
            with self._lock:
                self.utts.append(u)
                self.sink.emit(u, seg=seg.seg, timing=timing)
                row = {"type": "utterance", "seg": seg.seg, "id": u.id, "t0": round(u.t0, 3), "t1": round(u.t1, 3),
                       "t_unix": round(now, 4), **timing}
                if self.live and self._clock0 is not None:
                    row["t_emit"] = round(now - self._clock0, 3)
                self.sink.trace("events", row)
                self.stats["utterances"] += 1

    def feed(self, chunk: AudioChunk) -> None:
        if self._err is not None:
            raise self._err
        if self.live:
            c0 = time.time() - chunk.t1
            self._clock0 = c0 if self._clock0 is None else min(self._clock0, c0)
        self.stats["chunks"] += 1
        self.stats["audio_s"] = chunk.t1
        self.buf.push(chunk)
        self._out("audio", chunk)
        with self._spk_lock:
            self.speaker.feed(chunk)
        if self._inbox:
            self._apply_sets()
        segs = self.vad.feed(chunk)
        self._vad_events()  # STT より先に配る
        if self.partial_on:
            self._maybe_partial(chunk.t1)
        self._drain((self.vad, self.speaker) if self.stt_thread else None)
        for seg in segs:
            self._segment(seg)

    def _drain(self, provs=None) -> None:
        for prov in provs or (self.vad, self.stt, self.speaker, self.phones):
            for stage, row in getattr(prov, "drain_trace", lambda: [])():
                self._out("trace", stage, row)

    def finish(self) -> None:
        segs = self.vad.flush()
        self._vad_events()
        self._drain((self.vad, self.speaker) if self.stt_thread else None)
        for seg in segs:
            self._segment(seg)
        if self._worker is not None:
            self._q.put(None)
            self._worker.join()
            self._worker = None
        if self._err is not None:
            raise self._err
        revised = self.speaker.finalize(self.utts)
        self._drain()
        if revised is not self.utts:  # 付け直した provider だけ新しい列を返す
            self.utts = revised
            self.stats["utterances"] = len(revised)
            self._out("revise", self.utts)

    def run(self, chunks: Iterable[AudioChunk], source: str) -> dict[str, Any]:
        t_start = time.monotonic()
        self.live = source in ("mic", "ja1_udp") or source.endswith(":realtime")
        self._out("start", self.info(source))
        try:
            for c in chunks:
                self.feed(c)
            self.finish()
            self.stats["wall_s"] = round(time.monotonic() - t_start, 3)
            self.stats["audio_s"] = round(self.stats["audio_s"], 3)
            self._out("end", self.stats)
        finally:
            if self._worker is not None:  # 途中で例外が出たとき
                self._q.put(None)
                self._worker.join(timeout=60)
            self._out("close")
        return self.stats
