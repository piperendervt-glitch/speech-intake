from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from ..schema import Word


@dataclass
class SttResult:
    t0: float  # セッション開始からの秒
    t1: float
    text: str
    lang: str | None = None
    words: list[Word] = field(default_factory=list)


class SttProvider(Protocol):
    name: str  # 発話レコードの "stt" 欄に入る名前（例 "faster-whisper-large-v3"）

    def transcribe(self, audio: np.ndarray, t0: float, sample_rate: int) -> list[SttResult]:
        """audio（区間の音声）を文字にする。返す時刻はセッション開始からの秒（t0 を足したもの）。"""
        ...


class NoStt:
    """STT なし。VAD 区間を空文字の発話として 1 つ返す。"""

    name = "none"

    def transcribe(self, audio: np.ndarray, t0: float, sample_rate: int) -> list[SttResult]:
        return [SttResult(t0=t0, t1=t0 + len(audio) / sample_rate, text="")]
