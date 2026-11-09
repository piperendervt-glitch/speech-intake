"""段をつなぐ。入口（capture / replay）から AudioChunk を受け、vad → stt → speaker → sink と流す。

各段は関数境界で独立に呼べる（Lumi Bench 向け）：
  vad.feed(chunk) / stt.transcribe(audio, t0, sr) / speaker.assign(utts) / sink.emit(utt)
"""

from __future__ import annotations

import time
from collections import deque
from datetime import datetime
from typing import Any, Iterable

import numpy as np

from . import __version__, registry
from .audio import AudioChunk
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
        self.buf = AudioBuffer(self.sr)
        self.utts: list[Utterance] = []
        self._n = 0
        self.stats = {"chunks": 0, "audio_s": 0.0, "vad_segments": 0, "utterances": 0}

    def info(self, source: str) -> dict[str, Any]:
        public = {k: v for k, v in self.cfg.items() if not k.startswith("_")}
        return {
            "session": self.session,
            "run": self.run_name,
            "source": source,
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "intake_version": __version__,
            "schema_version": SCHEMA_VERSION,
            "providers": {"vad": self.vad.name, "stt": self.stt.name, "speaker": self.speaker.name},
            "config": public,
        }

    def _segment(self, seg) -> None:
        self.stats["vad_segments"] += 1
        self.sink.trace("vad", seg.to_dict())
        audio = self.buf.slice(seg.t0, seg.t1)
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
        self.speaker.assign(new)
        for u in new:
            self.utts.append(u)
            self.sink.emit(u)
        self.stats["utterances"] += len(new)

    def feed(self, chunk: AudioChunk) -> None:
        self.stats["chunks"] += 1
        self.stats["audio_s"] = chunk.t1
        self.buf.push(chunk)
        self.sink.audio(chunk)
        self.speaker.feed(chunk)
        segs = self.vad.feed(chunk)
        self._drain()
        for seg in segs:
            self._segment(seg)

    def _drain(self) -> None:
        for prov in (self.vad, self.stt, self.speaker):
            for stage, row in getattr(prov, "drain_trace", lambda: [])():
                self.sink.trace(stage, row)

    def finish(self) -> None:
        segs = self.vad.flush()
        self._drain()
        for seg in segs:
            self._segment(seg)
        revised = self.speaker.finalize(self.utts)
        self._drain()
        if revised is not self.utts:  # 付け直した provider だけ新しい列を返す
            self.utts = revised
            self.stats["utterances"] = len(revised)
            self.sink.revise(self.utts)

    def run(self, chunks: Iterable[AudioChunk], source: str) -> dict[str, Any]:
        t_start = time.monotonic()
        self.sink.start(self.info(source))
        try:
            for c in chunks:
                self.feed(c)
            self.finish()
            self.stats["wall_s"] = round(time.monotonic() - t_start, 3)
            self.stats["audio_s"] = round(self.stats["audio_s"], 3)
            self.sink.end(self.stats)
        finally:
            self.sink.close()
        return self.stats
