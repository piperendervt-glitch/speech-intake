"""音素の正規化：IPA / ARPAbet を粗い音素クラス（classes.yaml）に丸める。

  table = load_table()                       # 既定は intake/phones/classes.yaml。パスを渡せば差し替え
  split_ipa("pʰliːz kɔːl")                   # → ["pʰ", "l", "iː", "z", " ", "k", "ɔː", "l"]（空白 = 語の区切り）
  table.classify_ipa(["t", "ʃ", "iː"])       # → [CPhone("CH", "tʃ", (0, 1)), CPhone("IY", "iː", (2,))]
  table.classify_arpabet(["HH", "AH0", "L"]) # → HH AH L

どの元の音素（の何番目）から来たかを src に持つので、整列した結果を元の時刻・元の表記に戻せる。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_TABLE = Path(__file__).with_name("classes.yaml")
UNKNOWN = "?"
_ARPA_STRESS = re.compile(r"[0-9]+$")
# 前の音素に付く修飾文字（基底の文字ではない）。split_ipa で前の音素にくっつける
_MODIFIERS = set("ːˑʰʲʷˠˤⁿˡʼ˞")
_TIE = "͜͡"
_STRESS = "ˈˌ"


@dataclass
class CPhone:
    cls: str            # 粗いクラス（表に無ければ "?<記号>"）
    orig: str           # 元の表記（まとめた分は連結）
    src: tuple[int, ...]  # 元の音素列での位置


def _is_combining(ch: str) -> bool:
    return unicodedata.combining(ch) > 0


def split_ipa(s: str) -> list[str]:
    """IPA の文字列を音素の単位に切る。補助記号は前の文字に付け、結合子（t͡ʃ）は次の文字とつなぐ。

    強勢記号は次の音素の頭に付ける。空白は語の区切りとして " " を 1 つ残す。
    """
    out: list[str] = []
    cur = ""
    pending = ""  # 強勢記号など、次の音素の頭に付けるもの
    join_next = False
    for ch in unicodedata.normalize("NFD", s):
        if ch.isspace():
            if cur:
                out.append(cur)
                cur = ""
            if out and out[-1] != " ":
                out.append(" ")
            continue
        if ch in _STRESS:
            pending += ch
            continue
        if ch in _TIE:
            cur += ch
            join_next = True
            continue
        if _is_combining(ch) or ch in _MODIFIERS:
            if cur:
                cur += ch
            else:
                pending += ch
            continue
        if join_next and cur:
            cur += ch
            join_next = False
            continue
        if cur:
            out.append(cur)
        cur = pending + ch
        pending = ""
    if cur:
        out.append(cur)
    while out and out[-1] == " ":
        out.pop()
    return [unicodedata.normalize("NFC", x) if x != " " else x for x in out]


class ClassTable:
    def __init__(self, data: dict):
        self.classes: list[str] = list(data.get("classes") or [])
        self.drop: set[str] = set(data.get("drop") or [])
        self.ipa: dict[str, list[str]] = {unicodedata.normalize("NFC", str(k)): str(v).split() for k, v in (data.get("ipa") or {}).items()}
        self.arpa: dict[str, list[str]] = {str(k).upper(): str(v).split() for k, v in (data.get("arpabet") or {}).items()}
        self.maxlen = max((len(k) for k in self.ipa), default=1)
        bad = {c for vs in list(self.ipa.values()) + list(self.arpa.values()) for c in vs} - set(self.classes)
        if self.classes and bad:
            raise ValueError(f"classes.yaml: classes に無いクラスが対応表にある: {sorted(bad)}")

    def strip(self, ph: str) -> str:
        """補助記号を落とした基底の文字列。"""
        s = unicodedata.normalize("NFD", ph)
        s = "".join(ch for ch in s if not _is_combining(ch) and ch not in self.drop)
        return unicodedata.normalize("NFC", s)

    def classify_ipa(self, phones: list[str]) -> list[CPhone]:
        """音素の列（認識器のトークン列か split_ipa の結果）をクラスの列に。

        隣り合う音素を表の長いものから当てる（a + ɪ → AY）。" " は語の区切りで、それをまたいではまとめない。
        """
        # 各音素を基底の文字に分解し、どの音素から来た文字かを覚える
        chars: list[tuple[str, int]] = []
        for i, ph in enumerate(phones):
            if ph == " ":
                chars.append((" ", i))
                continue
            for ch in self.strip(ph):
                chars.append((ch, i))
        out: list[CPhone] = []
        k = 0
        while k < len(chars):
            ch, i = chars[k]
            if ch == " ":
                k += 1
                continue
            hit = None
            for n in range(min(self.maxlen, len(chars) - k), 0, -1):
                seg = chars[k : k + n]
                if any(c == " " for c, _ in seg):
                    continue
                key = "".join(c for c, _ in seg)
                if key in self.ipa:
                    hit = (n, self.ipa[key])
                    break
            n, cls = hit if hit else (1, [UNKNOWN + ch])
            src = tuple(sorted({j for _, j in chars[k : k + n]}))
            orig = "".join(phones[j] for j in src)
            for c in cls:
                out.append(CPhone(c, orig, src))
            k += n
        return out

    def classify_arpabet(self, phones: list[str]) -> list[CPhone]:
        out: list[CPhone] = []
        for i, ph in enumerate(phones):
            if ph.strip() == "":
                continue
            key = _ARPA_STRESS.sub("", ph.strip().upper())
            cls = self.arpa.get(key)
            for c in cls or [UNKNOWN + ph]:
                out.append(CPhone(c, ph, (i,)))
        return out

    def classify(self, phones: list[str], notation: str = "ipa") -> list[CPhone]:
        return self.classify_arpabet(phones) if notation == "arpabet" else self.classify_ipa(phones)


_CACHE: dict[str, ClassTable] = {}


def load_table(path: str | Path | None = None) -> ClassTable:
    p = str(path or DEFAULT_TABLE)
    if p not in _CACHE:
        _CACHE[p] = ClassTable(yaml.safe_load(Path(p).read_text(encoding="utf-8")) or {})
    return _CACHE[p]


def looks_arpabet(phones: list[str]) -> bool:
    toks = [p for p in phones if p.strip()]
    return bool(toks) and all(re.fullmatch(r"[A-Z]{1,3}[0-9]?", p) for p in toks)
