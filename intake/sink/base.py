from __future__ import annotations

from typing import Any, Protocol

from ..audio import AudioChunk
from ..schema import Utterance


class Sink(Protocol):
    def start(self, info: dict[str, Any]) -> None: ...
    def audio(self, chunk: AudioChunk) -> None: ...
    def trace(self, stage: str, obj: dict[str, Any]) -> None: ...
    def emit(self, utt: Utterance) -> None: ...
    def revise(self, utts: list[Utterance]) -> None: ...
    def close(self) -> None: ...


class MultiSink:
    def __init__(self, sinks: list[Any]):
        self.sinks = sinks

    def __getattr__(self, name: str):
        def call(*a: Any, **kw: Any) -> None:
            for s in self.sinks:
                getattr(s, name)(*a, **kw)

        return call
