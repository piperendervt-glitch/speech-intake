"""faster-whisper による文字起こし（単語ごとの時刻つき）。GPU があれば GPU を使う。

モデルは初回に HuggingFace から取得される（large-v3 は約 3 GB）。取得先は download_root で変えられる。
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ..schema import Word
from .base import SttResult


def _add_cuda_dlls() -> None:
    """Windows で pip の nvidia-cublas-cu12 / nvidia-cudnn-cu12 を入れた場合、その DLL を見えるようにする。"""
    import os  # noqa: PLC0415
    import sys  # noqa: PLC0415

    if sys.platform != "win32":
        return
    import importlib.util  # noqa: PLC0415

    for mod in ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_runtime"):
        try:
            spec = importlib.util.find_spec(mod)
        except ModuleNotFoundError:
            continue
        for loc in (spec.submodule_search_locations or []) if spec else []:
            b = os.path.join(loc, "bin")
            if os.path.isdir(b):
                os.add_dll_directory(b)
                os.environ["PATH"] = b + os.pathsep + os.environ.get("PATH", "")


def _pick_device(device: str, compute_type: str) -> tuple[str, str]:
    _add_cuda_dlls()
    if device == "auto":
        try:
            import ctranslate2  # noqa: PLC0415

            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:  # noqa: BLE001
            device = "cpu"
    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"
    return device, compute_type


class FasterWhisperStt:
    """VAD 区間の音声を 1 つずつ文字にする。

    model         large-v3 / large-v3-turbo / medium / small など（faster-whisper の名前かローカルのパス）
    language      "ja" など。null で自動判定（判定した言語を lang 欄に入れる）
    one_per_vad   true なら VAD 区間 1 つにつき発話レコード 1 つ（Whisper の文区切りを結合）。
                  false なら Whisper の区切り（segment）ごとに 1 つ
    Whisper 内蔵の VAD は使わない（区間は intake.vad が決める）。前の区間の文字を次の区間の手がかりにもしない
    （condition_on_previous_text=false。区間ごとに独立させ、比較しやすくするため）。
    """

    def __init__(
        self,
        model: str = "large-v3",
        device: str = "auto",
        compute_type: str = "auto",
        language: str | None = "ja",
        beam_size: int = 5,
        one_per_vad: bool = False,
        initial_prompt: str | None = None,
        download_root: str | None = None,
        local_files_only: bool = False,
        cpu_threads: int = 0,
        _model: Any = None,  # テスト用の差し込み口
    ):
        self.device, self.compute_type = _pick_device(device, compute_type)
        label = model.replace("\\", "/").rstrip("/").split("/")[-1]
        self.name = f"faster-whisper-{label}"
        self.language = language
        self.beam_size = beam_size
        self.one_per_vad = one_per_vad
        self.initial_prompt = initial_prompt
        if _model is not None:
            self.model = _model
        else:
            from faster_whisper import WhisperModel  # noqa: PLC0415

            self.model = WhisperModel(
                model,
                device=self.device,
                compute_type=self.compute_type,
                download_root=download_root,
                local_files_only=local_files_only,
                cpu_threads=cpu_threads,
            )

    def transcribe(self, audio: np.ndarray, t0: float, sample_rate: int) -> list[SttResult]:
        if sample_rate != 16000:
            raise ValueError("faster-whisper には 16 kHz を渡す")
        dur = len(audio) / sample_rate
        if dur < 0.1:
            return []
        segments, info = self.model.transcribe(
            audio.astype(np.float32),
            language=self.language,
            beam_size=self.beam_size,
            word_timestamps=True,
            vad_filter=False,
            condition_on_previous_text=False,
            initial_prompt=self.initial_prompt,
        )
        lang = getattr(info, "language", None) or self.language
        out: list[SttResult] = []
        for s in segments:
            words = [
                Word(w=w.word.strip(), t0=t0 + min(max(w.start, 0.0), dur), t1=t0 + min(max(w.end, 0.0), dur), p=w.probability)
                for w in (s.words or [])
                if w.word.strip()
            ]
            text = s.text.strip()
            if not text:
                continue
            a = t0 + min(max(s.start, 0.0), dur)
            b = t0 + min(max(s.end, 0.0), dur)
            out.append(SttResult(t0=a, t1=max(a, b), text=text, lang=lang, words=words))
        if self.one_per_vad and len(out) > 1:
            sep = "" if (lang or "").startswith(("ja", "zh")) else " "
            out = [SttResult(t0=out[0].t0, t1=out[-1].t1, text=sep.join(r.text for r in out), lang=lang,
                             words=[w for r in out for w in r.words])]
        return out
