from __future__ import annotations

from typing import Protocol

from ..audio import AudioChunk
from ..schema import VadSegment


class VadProvider(Protocol):
    name: str  # 発話レコードの "vad" 欄に入る名前（例 "silero-v5"）

    def feed(self, chunk: AudioChunk) -> list[VadSegment]:
        """チャンクを受け、閉じた（終わりが確定した）区間を返す。"""
        ...

    def flush(self) -> list[VadSegment]:
        """入力の終わり。開いている区間を閉じて返す。"""
        ...


class NoVad:
    """VAD なし。区間を出さない（段1用）。"""

    name = "none"

    def feed(self, chunk: AudioChunk) -> list[VadSegment]:  # noqa: ARG002
        return []

    def flush(self) -> list[VadSegment]:
        return []
