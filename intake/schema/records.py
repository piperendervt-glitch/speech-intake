"""発話レコード（観察）と推察レコードの dataclass 定義。

時刻はすべて「セッション開始からの秒」（音声サンプルの時計が唯一の基準）。
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = "0.2"

UTT_ID_RE = re.compile(r"^utt_\d{6,}$")
SESSION_RE = re.compile(r"^\d{8}_\d{6}(?:_[A-Za-z0-9-]+)?$")
SPEAKER_SOURCES = ("acoustic", "wearer", "face", "script", "none")  # script = 合成サンプルの台本（正解）
# phones の出どころ。model = 音素認識器が聞き取ったもの（観察）。human = 人間の音声転写、g2p = 文字からの辞書変換。
# human / g2p は観察ではなく「参照」なので、通常の run には現れず、参照 run（ref / ref_g2p）にだけ入る。
PHONES_SOURCES = ("model", "human", "g2p", "none")


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
class Phone:
    """音素 1 つ。ph は IPA 文字列（認識器・転写の表記そのまま）。

    時刻はセッション秒。参照（human / g2p）には時刻が無いことがあり、そのときは None。
    p は認識器が出した確率をそのまま転記（解釈ではない）。
    w は参照だけが持つ「text を空白で切った何番目の語か」（認識器の音素には語の区切りが無いので持たない）。
    """

    ph: str
    t0: float | None = None
    t1: float | None = None
    p: float | None = None
    w: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"ph": self.ph}
        d["t0"] = None if self.t0 is None else round(self.t0, 3)
        d["t1"] = None if self.t1 is None else round(self.t1, 3)
        if self.p is not None:
            d["p"] = round(self.p, 3)
        if self.w is not None:
            d["w"] = self.w
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Phone":
        t0, t1 = d.get("t0"), d.get("t1")
        return cls(ph=d["ph"], t0=None if t0 is None else float(t0), t1=None if t1 is None else float(t1),
                   p=d.get("p"), w=d.get("w"))


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
    # schema 0.2：どう発音されたか（音素列）。欄が無い 0.1 のレコードは空として読む
    phones: list[Phone] = field(default_factory=list)
    phones_model: str | None = None
    phones_source: str = "none"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["t0"] = round(self.t0, 3)
        d["t1"] = round(self.t1, 3)
        d["words"] = [w.to_dict() for w in self.words]
        d["phones"] = [p.to_dict() for p in self.phones]
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
            phones=[Phone.from_dict(p) for p in d.get("phones") or []],
            phones_model=d.get("phones_model"),
            phones_source=d.get("phones_source", "none"),
        )


@dataclass
class VadSegment:
    """VAD が出した発話区間。観察表ではなく、閲覧画面で確かめるための段ごとの記録（trace）。"""

    t0: float
    t1: float
    vad: str
    p: float | None = None
    seg: int | None = None  # 区間の通し番号（配信の speech_start / speech_end / utterance と対応を取るため）

    def to_dict(self) -> dict[str, Any]:
        d = {"t0": round(self.t0, 3), "t1": round(self.t1, 3), "vad": self.vad}
        if self.p is not None:
            d["p"] = round(self.p, 3)
        if self.seg is not None:
            d["seg"] = self.seg
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
    psrc = d.get("phones_source", "none")
    if psrc not in PHONES_SOURCES:
        raise SchemaError(f"phones_source は {PHONES_SOURCES} のどれか: {psrc!r}")
    for ph in d.get("phones") or []:
        if not isinstance(ph, dict) or not isinstance(ph.get("ph"), str) or not ph["ph"]:
            raise SchemaError(f"phones の要素は ph（IPA 文字列）を持つ: {ph!r}")
        for k in ("t0", "t1"):
            if ph.get(k) is not None:
                _check_time(f"phones.{k}", ph[k])
        if psrc == "model" and (ph.get("t0") is None or ph.get("t1") is None):
            raise SchemaError(f"認識器の音素（phones_source=model）は時刻を持つ: {ph!r}")


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
