"""装着者判定（スタブ）。判定そのものはしない。外部プロセス（subvox など）から
「装着者が話していた区間 t0, t1」を受け取り、発話レコードとの重なりで speaker を付けるだけ。

区間の受け取り口は 2 つ（どちらも時刻はセッション開始からの秒）:
  1. ファイル: path に JSONL を追記してもらう。1 行 = {"t0": 12.3, "t1": 13.9}（"conf", "by" は任意）
     相対パスはセッションのフォルダ基準（既定 sessions/<session>/wearer.jsonl）。
  2. WebSocket: 配信口（ws://127.0.0.1:8766/ws）に {"type": "wearer", "t0": 12.3, "t1": 13.9} を送る。
     （sink.websocket.enabled: true のときだけ）

付け方: 発話の時間のうち装着者区間と重なる割合 r を測り、
  r >= min_ratio なら speaker="wearer", speaker_conf=r
  r <  min_ratio なら speaker="not_wearer", speaker_conf=1-r
speaker_source は "wearer"。割合は重なりの長さの比で、確率ではない。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from ..audio import AudioChunk
from ..schema import Utterance


class WearerSpeaker:
    name = "wearer-stub"
    source = "wearer"

    def __init__(self, path: str | None = "wearer.jsonl", min_ratio: float = 0.5, session_dir: str | None = None):
        self.min_ratio = min_ratio
        self.path: Path | None = None
        if path:
            p = Path(path)
            self.path = p if p.is_absolute() or session_dir is None else Path(session_dir) / p
        self._intervals: list[tuple[float, float]] = []
        self._lock = threading.Lock()
        self._file_pos = 0
        self._trace: list[tuple[str, dict]] = []

    # ---- 区間の受け取り ----
    def add_interval(self, t0: float, t1: float, **extra: Any) -> None:
        if not (0 <= t0 <= t1):
            raise ValueError(f"区間が不正: {t0}, {t1}")
        with self._lock:
            self._intervals.append((float(t0), float(t1)))
        self._trace.append(("speaker", {"t0": round(t0, 3), "t1": round(t1, 3), "speaker": "wearer", "by": extra.get("by", "external")}))

    def on_message(self, m: dict[str, Any]) -> None:
        if m.get("type") == "wearer":
            self.add_interval(m["t0"], m["t1"], by=m.get("by", "ws"))

    def _read_file(self) -> None:
        if not self.path or not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as f:
            f.seek(self._file_pos)
            while True:
                line = f.readline()
                if not line or not line.endswith("\n"):  # 書きかけの行は次回
                    break
                self._file_pos = f.tell()
                if line.strip():
                    d = json.loads(line)
                    self.add_interval(d["t0"], d["t1"], by=d.get("by", "file"))

    # ---- SpeakerProvider ----
    def feed(self, chunk: AudioChunk) -> None:  # noqa: ARG002
        return None

    def _label(self, u: Utterance) -> None:
        with self._lock:
            ov = sum(max(0.0, min(b, u.t1) - max(a, u.t0)) for a, b in _merge(self._intervals))
        r = min(1.0, ov / max(u.t1 - u.t0, 1e-6))
        if r >= self.min_ratio:
            u.speaker, u.speaker_conf = "wearer", r
        else:
            u.speaker, u.speaker_conf = "not_wearer", 1.0 - r
        u.speaker_source = self.source

    def assign(self, utts: list[Utterance]) -> None:
        self._read_file()
        for u in utts:
            self._label(u)

    def finalize(self, utts: list[Utterance]) -> list[Utterance]:
        """後から届いた区間も含めて付け直す。"""
        self._read_file()
        out = list(utts)
        for u in out:
            self._label(u)
        return out

    def drain_trace(self) -> list[tuple[str, dict]]:
        t, self._trace = self._trace, []
        return t


def _merge(iv: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for a, b in sorted(iv):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out
