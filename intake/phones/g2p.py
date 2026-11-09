"""文字 → 辞書上の音素（G2P）。参照 run ref_g2p を作るためのもの。

これは「辞書上はこう読むはず」であって、観察ではない。発話レコードには phones_source: g2p として、参照 run にだけ書く。

  engine=g2p_en   pip install g2p_en（CMUdict ＋ 未知語は学習済みモデル。ARPAbet を出す）
  engine=espeak   espeak-ng（IPA を出す。Windows は別にインストール）
"""

from __future__ import annotations

import re
import shutil
import subprocess

from ..schema import Phone

_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*|\d+")


def words_of(text: str) -> list[str]:
    """text を空白で切った語（Phone.w の番号はこの並び）。"""
    return text.split()


class G2P:
    def __init__(self, engine: str = "g2p_en", voice: str = "en-us"):
        self.engine = engine
        self.voice = voice
        if engine == "g2p_en":
            from g2p_en import G2p  # noqa: PLC0415

            self._g = G2p()
            self.name = "g2p_en"
        elif engine == "espeak":
            self._exe = shutil.which("espeak-ng") or shutil.which("espeak")
            if not self._exe:
                raise SystemExit("espeak-ng が見つからない（g2p_en を使うか、espeak-ng を入れる）")
            self.name = f"espeak-ng-{voice}"
        else:
            raise ValueError(f"g2p engine: {engine!r}")

    def word(self, w: str, pos: str | None = None) -> list[str]:
        core = "".join(_WORD.findall(w.replace("’", "'")))
        if not core:
            return []
        if self.engine == "g2p_en":
            # 同形異音語（read, live など）は品詞で読み分ける（g2p_en と同じ表）。それ以外は CMUdict の第 1 候補、
            # 無ければ学習済みモデルの推定
            homo = getattr(self._g, "homograph2features", {}).get(core.lower())
            if homo and pos:
                pron1, pron2, pos1 = homo
                return list(pron1 if pos.startswith(pos1) else pron2)
            cmu = getattr(self._g, "cmu", None)
            prons = cmu.get(core.lower()) if cmu is not None else None
            if prons:
                phs = prons[0]
            elif hasattr(self._g, "predict"):
                phs = self._g.predict(core.lower())
            else:
                phs = self._g(core)
            return [p for p in phs if p.strip() and re.fullmatch(r"[A-Z]+[0-9]?", p)]
        out = subprocess.run([self._exe, "-q", "--ipa", "--sep= ", "-v", self.voice, core],
                             capture_output=True, text=True, encoding="utf-8", check=True).stdout
        return [p for p in out.replace("\n", " ").split(" ") if p.strip()]

    def _pos(self, words: list[str]) -> list[str | None]:
        if self.engine != "g2p_en":
            return [None] * len(words)
        try:
            from nltk import pos_tag  # noqa: PLC0415

            cores = ["".join(_WORD.findall(w.replace("’", "'"))) or "," for w in words]
            return [t for _, t in pos_tag(cores)]
        except Exception:  # noqa: BLE001
            return [None] * len(words)

    def phones(self, text: str) -> list[Phone]:
        out: list[Phone] = []
        words = words_of(text)
        for i, (w, pos) in enumerate(zip(words, self._pos(words))):
            out.extend(Phone(ph=p, w=i) for p in self.word(w, pos))
        return out
