"""facebook/wav2vec2-lv-60-espeak-cv-ft による音素認識（IPA 出力、eSpeak の音素表記）。

モデルは初回だけ HuggingFace から取得する（約 1.3 GB、トークン不要）。2 回目以降は手元のファイルだけを使い、
ネットに問い合わせない。torch と transformers が要る（pip install -e .[phones]）。

CTC の出力を自前で貪欲に復号し、各音素が出た枠（20 ms）から時刻を、その枠の最大確率の平均から p を出す。
transformers の tokenizer（phonemizer と espeak-ng が要る）は使わず、vocab.json だけを読む。
"""

from __future__ import annotations

import json
import math
import os
from typing import Any

import numpy as np

from ..schema import Phone
from .base import pick_torch_device

SPECIAL = {"<pad>", "<s>", "</s>", "<unk>", "|", ""}


def ctc_greedy(probs: np.ndarray, vocab: dict[int, str], blank: int, t0: float, stride: float) -> list[Phone]:
    """probs: (枠数, 語彙) の確率。連続する同じ id をまとめ、blank と特殊記号を除く。"""
    ids = probs.argmax(axis=1)
    pmax = probs.max(axis=1)
    out: list[Phone] = []
    i, n = 0, len(ids)
    while i < n:
        j = i
        while j + 1 < n and ids[j + 1] == ids[i]:
            j += 1
        tok = vocab.get(int(ids[i]), "")
        if int(ids[i]) != blank and tok not in SPECIAL:
            out.append(Phone(ph=tok, t0=t0 + i * stride, t1=t0 + (j + 1) * stride, p=float(pmax[i : j + 1].mean())))
        i = j + 1
    return out


class Wav2Vec2EspeakPhones:
    """VAD 区間の音声を音素列にする。

    model    HuggingFace の名前かローカルのパス
    device   auto（GPU があれば cuda）/ cuda / cpu。GPU は STT 優先なので、足りなければ cpu に
    local_files_only  true なら取得もしない（完全にオフライン）
    """

    def __init__(self, model: str = "facebook/wav2vec2-lv-60-espeak-cv-ft", device: str = "auto",
                 local_files_only: bool = False, _model: Any = None, _vocab: dict[int, str] | None = None):
        self.name = model.replace("\\", "/").rstrip("/").split("/")[-1]
        self.device = pick_torch_device(device)
        if _model is not None:  # テスト用の差し込み口
            self.model, self.vocab, self.blank, self.stride, self.fe = _model, _vocab or {}, 0, 0.02, None
            return
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        try:
            self._load(model, True)  # 手元にあればネットに問い合わせない
        except Exception:  # noqa: BLE001
            if local_files_only:
                raise
            print(f"[phones] {model} が手元に無いので HuggingFace から取得する（初回のみ、約 1.3 GB）")
            self._load(model, False)

    def _load(self, model: str, local: bool) -> None:
        from transformers import Wav2Vec2FeatureExtractor, Wav2Vec2ForCTC  # noqa: PLC0415

        self.fe = Wav2Vec2FeatureExtractor.from_pretrained(model, local_files_only=local)
        self.model = Wav2Vec2ForCTC.from_pretrained(model, local_files_only=local).to(self.device).eval()
        if os.path.isdir(model):
            vpath = os.path.join(model, "vocab.json")
        else:
            from huggingface_hub import hf_hub_download  # noqa: PLC0415

            vpath = hf_hub_download(model, "vocab.json", local_files_only=local)
        with open(vpath, encoding="utf-8") as f:
            v = json.load(f)
        if v and all(isinstance(x, dict) for x in v.values()):  # 言語ごとに分かれた形
            v = next(iter(v.values()))
        self.vocab = {int(i): t for t, i in v.items()}
        self.blank = int(self.model.config.pad_token_id or 0)
        self.stride = math.prod(self.model.config.conv_stride) / 16000

    def logits_to_probs(self, audio: np.ndarray) -> np.ndarray:
        import torch  # noqa: PLC0415

        x = self.fe(audio.astype(np.float32), sampling_rate=16000, return_tensors="pt").input_values.to(self.device)
        with torch.inference_mode():
            logits = self.model(x).logits[0]
        return torch.softmax(logits.float(), dim=-1).cpu().numpy()

    def recognize(self, audio: np.ndarray, t0: float, sample_rate: int) -> list[Phone]:
        if sample_rate != 16000:
            raise ValueError("wav2vec2 には 16 kHz を渡す")
        if len(audio) < 0.1 * sample_rate:
            return []
        probs = self.model(audio) if callable(self.model) and self.fe is None else self.logits_to_probs(audio)
        return ctc_greedy(np.asarray(probs), self.vocab, self.blank, t0, self.stride)
