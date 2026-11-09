"""段と段の間を流れる音声の単位。

すべての段（vad / stt / speaker / sink）はこの AudioChunk の列だけを受け取り、
マイクにも WAV ファイルにも直接は触らない。入口は capture（マイク）と replay（WAV）の 2 つ。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Protocol

import numpy as np

SAMPLE_RATE = 16000


@dataclass
class AudioChunk:
    t0: float  # セッション開始からの秒（= 先頭サンプル番号 / sample_rate）
    samples: np.ndarray  # float32 mono, -1..1
    sample_rate: int = SAMPLE_RATE

    @property
    def t1(self) -> float:
        return self.t0 + len(self.samples) / self.sample_rate


class AudioSource(Protocol):
    session: str
    sample_rate: int

    def chunks(self) -> Iterator[AudioChunk]: ...
