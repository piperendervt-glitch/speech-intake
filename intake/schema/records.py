"""発話レコード（観察）と推察レコードの dataclass 定義。

時刻はすべて「セッション開始からの秒」（音声サンプルの時計が唯一の基準）。
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = "0.1"

UTT_ID_RE = re.compile(r"^utt_\d{6,}$")
SESSION_RE = re.compile(r"^\d{8}_\d{6}(?:_[A-Za-z0-9-]+)?$")
SPEAKER_SOURCES = ("acoustic", "wearer", "face", "none")


class SchemaError(ValueError):
    """レコードが規約に合わないとき。"""


def make_utterance_id(n: int) -> str:
    return f"utt_{n:06d}"


def _check_time(name: str, v: Any) -> None:
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v < 0:
        raise SchemaError(f"{name} はセッション開始からの秒（0 以上の有限数）: {v!r}")


@dataclass
class Word:
    w: str
    t0: float
    t1: float
    p: float | None = None  # STT が出した単語確率（そのまま転記。解釈ではない）

    def to_dict(self) -> dict[str, Any]:
        d = {"w": self.w, "t0": round(self.t0, 3), "t1": round(self.t1, 3)}
        if self.p is not None:
            d["p"] = round(self.p, 3)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Word":
        return cls(w=d["w"], t0=float(d["t0"]), t1=float(d["t1"]), p=d.get("p"))


@dataclass
class Utterance:
    """観察表の 1 行。ここには観察（機械が測ったもの）だけを書く。"""

    id: str
    session: str
    t0: float
    t1: float
    text: str = ""
    lang: str | None = None
    speaker: str | None = None
    speaker_conf: float | None = None
    speaker_source: str = "none"
    words: list[Word] = field(default_factory=list)
    vad: str | None = None
    stt: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["t0"] = round(self.t0, 3)
        d["t1"] = round(self.t1, 3)
        d["words"] = [w.to_dict() for w in self.words]
        if self.speaker_conf is not None:
            d["speaker_conf"] = round(self.speaker_conf, 3)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Utterance":
        validate_utterance(d)
        return cls(
            id=d["id"],
            session=d["session"],
            t0=float(d["t0"]),
            t1=float(d["t1"]),
            text=d.get("text", ""),
            lang=d.get("lang"),
            speaker=d.get("speaker"),
            speaker_conf=d.get("speaker_conf"),
            speaker_source=d.get("speaker_source", "none"),
            words=[Word.from_dict(w) for w in d.get("words", [])],
            vad=d.get("vad"),
            stt=d.get("stt"),
        )


@dataclass
class VadSegment:
    """VAD が出した発話区間。観察表ではなく、閲覧画面で確かめるための段ごとの記録（trace）。"""

    t0: float
    t1: float
    vad: str
    p: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {"t0": round(self.t0, 3), "t1": round(self.t1, 3), "vad": self.vad}
        if self.p is not None:
            d["p"] = round(self.p, 3)
        return d


@dataclass
class Inference:
    """推察表の 1 行（利用側が書く）。本モジュールはこの型を提供するだけで、読み書きしない。

    refs: 根拠になった発話レコード id。1 つ以上必須。
    kind: 推察の種類（例 "intent", "summary", "context"）。利用側が決める。
    value: 推察の中身。
    by: 推察を出したもの（モデル名・人名など）。
    """

    id: str
    refs: list[str]
    kind: str
    value: Any
    by: str
    conf: float | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        validate_inference(asdict(self))
        return asdict(self)


def validate_utterance(d: dict[str, Any]) -> None:
    for k in ("id", "session", "t0", "t1"):
        if k not in d:
            raise SchemaError(f"発話レコードに {k} がない")
    if not UTT_ID_RE.match(str(d["id"])):
        raise SchemaError(f"id は utt_NNNNNN 形式: {d['id']!r}")
    if not SESSION_RE.match(str(d["session"])):
        raise SchemaError(f"session は YYYYMMDD_HHMMSS 形式: {d['session']!r}")
    _check_time("t0", d["t0"])
    _check_time("t1", d["t1"])
    if d["t1"] < d["t0"]:
        raise SchemaError(f"t1 < t0: {d['t0']} > {d['t1']}")
    src = d.get("speaker_source", "none")
    if src not in SPEAKER_SOURCES:
        raise SchemaError(f"speaker_source は {SPEAKER_SOURCES} のどれか: {src!r}")
    conf = d.get("speaker_conf")
    if conf is not None and not (0.0 <= float(conf) <= 1.0):
        raise SchemaError(f"speaker_conf は 0..1: {conf!r}")
    for w in d.get("words", []):
        for k in ("w", "t0", "t1"):
            if k not in w:
                raise SchemaError(f"words の要素に {k} がない: {w!r}")
        _check_time("words.t0", w["t0"])
        _check_time("words.t1", w["t1"])


def validate_inference(d: dict[str, Any]) -> None:
    refs = d.get("refs")
    if not isinstance(refs, list) or not refs:
        raise SchemaError("推察レコードは refs（発話レコード id の配列）を 1 つ以上必ず持つ")
    for r in refs:
        if not isinstance(r, str) or not UTT_ID_RE.match(r):
            raise SchemaError(f"refs の要素は utt_NNNNNN 形式: {r!r}")
    for k in ("id", "kind", "by"):
        if not d.get(k):
            raise SchemaError(f"推察レコードに {k} がない")
    if "value" not in d:
        raise SchemaError("推察レコードに value がない")
