from __future__ import annotations

from typing import Protocol

import numpy as np

from ..schema import Phone, Utterance


class PhonesProvider(Protocol):
    name: str  # 発話レコードの phones_model 欄に入る名前

    def recognize(self, audio: np.ndarray, t0: float, sample_rate: int) -> list[Phone]:
        """区間の音声を音素列にする。返す時刻はセッション秒（t0 を足したもの）。"""
        ...


class NoPhones:
    """音素認識なし（既定）。"""

    name = "none"

    def recognize(self, audio: np.ndarray, t0: float, sample_rate: int) -> list[Phone]:  # noqa: ARG002
        return []


def assign_phones(utts: list[Utterance], phones: list[Phone], model: str) -> list[Phone]:
    """VAD 区間 1 つ分の音素を、その区間から出た発話レコードに時刻で振り分ける。

    音素の中点が入る発話へ。どの発話にも入らない音素は最も近い発話へ。発話が無ければ振り分けずに返す。
    """
    if not utts:
        return list(phones)
    for u in utts:
        u.phones_model = model
        u.phones_source = "model"
    for ph in phones:
        mid = (ph.t0 + ph.t1) / 2
        best = min(utts, key=lambda u: 0.0 if u.t0 <= mid <= u.t1 else min(abs(mid - u.t0), abs(mid - u.t1)))
        best.phones.append(ph)
    for u in utts:
        u.phones.sort(key=lambda p: p.t0)
    return []


def pick_torch_device(device: str) -> str:
    if device != "auto":
        return device
    try:
        import torch  # noqa: PLC0415

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:  # noqa: BLE001
        return "cpu"
