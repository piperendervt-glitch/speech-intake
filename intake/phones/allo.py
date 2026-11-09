"""allosaurus による音素認識（IPA 出力）。pip install allosaurus（torch が要る）。

モデル（uni2005, 約 20 MB）は初回の実行で allosaurus が自分の置き場所に取得する。
lang に ISO 639-3（eng など）を渡すと、その言語の音素目録に絞って出す。ipa なら全言語共通の目録。
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from ..schema import Phone

_PROB = re.compile(r"(\S+)\s+\(([0-9.eE+-]+)\)")


def parse_timestamped(text: str, t0: float) -> list[Phone]:
    """allosaurus の timestamp=True の出力（1 行 = 開始 長さ 音素 [(確率) ...]）を Phone に。"""
    out: list[Phone] = []
    for line in text.splitlines():
        parts = line.strip().split(maxsplit=2)
        if len(parts) < 3:
            continue
        try:
            a, d = float(parts[0]), float(parts[1])
        except ValueError:
            continue
        rest = parts[2]
        m = _PROB.search(rest)
        ph, p = (m.group(1), float(m.group(2))) if m else (rest.split()[0], None)
        out.append(Phone(ph=ph, t0=t0 + a, t1=t0 + a + d, p=p))
    return out


class AllosaurusPhones:
    def __init__(self, model: str = "uni2005", lang: str = "eng", device: str = "auto", emit: float = 1.0,
                 topk: int = 2, _model: Any = None):
        self.lang = lang
        self.emit = emit
        self.topk = topk
        self.name = f"allosaurus-{model}-{lang}"
        if _model is not None:
            self.model = _model
            return
        from allosaurus.app import read_recognizer  # noqa: PLC0415

        from .base import pick_torch_device  # noqa: PLC0415

        dev = pick_torch_device(device)
        try:  # allosaurus は device_id（-1 = CPU）で GPU を選ぶ
            from argparse import Namespace  # noqa: PLC0415

            self.model = read_recognizer(Namespace(model=model, device_id=0 if dev == "cuda" else -1, lang=lang, approximate=False, prior=None))
        except Exception:  # noqa: BLE001
            self.model = read_recognizer(model)

    def recognize(self, audio: np.ndarray, t0: float, sample_rate: int) -> list[Phone]:
        if len(audio) < 0.1 * sample_rate:
            return []
        import soundfile as sf  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "seg.wav"
            sf.write(p, audio.astype(np.float32), sample_rate, subtype="PCM_16")
            text = self.model.recognize(str(p), self.lang, topk=self.topk, emit=self.emit, timestamp=True)
        return parse_timestamped(text, t0)
