"""段をつなぐ。入口（capture / replay）から AudioChunk を受け、vad → stt → phones → speaker → sink と流す。

各段は関数境界で独立に呼べる（Lumi Bench 向け）：
  vad.feed(chunk) / stt.transcribe(audio, t0, sr) / phones.recognize(audio, t0, sr) / speaker.assign(utts) / sink.emit(utt)

段10（ライブ会話向け）：VAD が区間の始まり・終わりを確定した時点で、STT を待たずに sink.event() で通知する
（speech_start / speech_end / speech_dropped）。pipeline.stt_thread: true なら STT 以降を別スレッドで回し、
STT の最中も VAD が止まらないようにする（次の speech_start が STT の終わりを待たない）。
通知と発話レコードの時刻の記録は trace/events.jsonl（閲覧画面で遅れを見る）。
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from datetime import datetime
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

    def info(self, source: str) -> dict[str, Any]:
        public = {k: v for k, v in self.cfg.items() if not k.startswith("_")}
        return {
            "session": self.session,
            "run": self.run_name,
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
        if self._worker is None:
            self._q = queue.Queue()
            self._worker = threading.Thread(target=self._work, name="intake-stt", daemon=True)
            self._worker.start()
        self._q.put(job)

    def _work(self) -> None:
        while True:
            job = self._q.get()
            if job is None:
                return
            try:
                self._transcribe(*job)
                self._drain((self.stt, self.phones))
            except BaseException as e:  # noqa: BLE001
                self._err = e
                return

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
        segs = self.vad.feed(chunk)
        self._vad_events()  # STT より先に配る
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
        self.live = source == "mic" or source.endswith(":realtime")
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
