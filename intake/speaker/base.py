from __future__ import annotations

from typing import Protocol

from ..audio import AudioChunk
from ..schema import Utterance


class SpeakerProvider(Protocol):
    name: str
    source: str  # 発話レコードの speaker_source 欄（"acoustic" / "wearer" / "face" / "none"）

    def feed(self, chunk: AudioChunk) -> None:
        """音声を順に受け取る（ストリーム型の provider 用。使わないなら何もしない）。"""
        ...

    def assign(self, utts: list[Utterance]) -> None:
        """発話レコードの speaker / speaker_conf / speaker_source を埋める（その場で書き換える）。"""
        ...

    def finalize(self, utts: list[Utterance]) -> list[Utterance]:
        """入力の終わり。セッション全体を見て付け直す provider はここで直した列を返す。"""
        ...


class NoSpeaker:
    name = "none"
    source = "none"

    def feed(self, chunk: AudioChunk) -> None:  # noqa: ARG002
        return None

    def assign(self, utts: list[Utterance]) -> None:
        for u in utts:
            u.speaker, u.speaker_conf, u.speaker_source = None, None, "none"

    def finalize(self, utts: list[Utterance]) -> list[Utterance]:
        return utts
