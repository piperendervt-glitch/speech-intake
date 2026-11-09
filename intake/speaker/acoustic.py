"""話者分離（声の特徴で話者を分ける）。

provider 名:
  acoustic           sherpa-onnx（pyannote segmentation 3.0 の ONNX + 3D-Speaker 話者埋め込み + クラスタリング）。既定。
                     モデルは GitHub（k2-fsa/sherpa-onnx の releases）から取得。HuggingFace のトークン不要、torch 不要。
  acoustic_pyannote  pyannote.audio の speaker-diarization-community-1。HuggingFace で利用条件への同意とトークンが要る。

共通の振る舞い:

- 音声は feed() で受け取って手元にためておき、入力の終わり（finalize）でセッション全体を一度に分離する。
  そのためライブ中に出る発話レコードの speaker は空で、録音を止めた時点で付け直した列（revise）が出る。
- 各発話レコードには、発話の時間と最も長く重なった話者を入れる。
  speaker_conf は「その話者が発話の時間に占める割合」（0..1。重なりの長さの比で、確率ではない）。
- 分離の結果（話者ごとの区間）は trace/speaker.jsonl に書く（閲覧画面で確かめるため）。

"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np

from ..audio import AudioChunk
from ..schema import Utterance


class AcousticSpeaker:
    """話者分離 provider の共通部分。diarize() だけを実装すればよい。"""

    source = "acoustic"
    name = "acoustic"

    def _init_common(self) -> None:
        self._chunks: list[np.ndarray] = []
        self.sr = 16000
        self._trace: list[tuple[str, dict]] = []

    def feed(self, chunk: AudioChunk) -> None:
        self.sr = chunk.sample_rate
        self._chunks.append((chunk.samples * 32767).astype(np.int16))  # メモリ節約（1 時間で約 115 MB）

    def assign(self, utts: list[Utterance]) -> None:
        for u in utts:  # ライブ中は未定。finalize で付け直す
            u.speaker, u.speaker_conf, u.speaker_source = None, None, self.source

    def diarize(self, audio: np.ndarray, sr: int) -> list[tuple[float, float, str]]:
        """音声全体を話者分離し、(t0, t1, 元のラベル) の列を返す。単独で呼べる関数境界。"""
        raise NotImplementedError

    def finalize(self, utts: list[Utterance]) -> list[Utterance]:
        if not self._chunks:
            return list(utts)
        audio = np.concatenate(self._chunks).astype(np.float32) / 32767
        turns = self.diarize(audio, self.sr)
        # 元のラベルを登場順に spk_1, spk_2, ... に付け替える
        names: dict[str, str] = {}
        for _, _, lbl in sorted(turns):
            names.setdefault(lbl, f"spk_{len(names) + 1}")
        turns = [(a, b, names[lbl]) for a, b, lbl in turns]
        for a, b, spk in sorted(turns):
            self._trace.append(("speaker", {"t0": round(a, 3), "t1": round(b, 3), "speaker": spk, "by": self.name}))
        out = []
        for u in utts:
            ov: dict[str, float] = {}
            for a, b, spk in turns:
                d = min(b, u.t1) - max(a, u.t0)
                if d > 0:
                    ov[spk] = ov.get(spk, 0.0) + d
            if ov:
                spk = max(ov, key=ov.get)
                u.speaker = spk
                u.speaker_conf = min(1.0, ov[spk] / max(u.t1 - u.t0, 1e-6))
            else:
                u.speaker, u.speaker_conf = None, None
            u.speaker_source = self.source
            out.append(u)
        return out

    def drain_trace(self) -> list[tuple[str, dict]]:
        t, self._trace = self._trace, []
        return t


MODELS_DIR = Path(os.environ.get("INTAKE_MODELS_DIR", "models"))
SHERPA_SEG = "speaker/sherpa-onnx-pyannote-segmentation-3-0/model.onnx"
SHERPA_EMB = "speaker/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"


class SherpaSpeaker(AcousticSpeaker):
    """sherpa-onnx の話者分離。モデルは python -m intake models で models/speaker/ に取得する。

    num_speakers  話者数が分かっていれば指定（-1 / null で自動）
    threshold     話者数が自動のとき、クラスタを分ける距離の閾値（小さいほど話者が増える）
    """

    def __init__(
        self,
        segmentation_model: str | None = None,
        embedding_model: str | None = None,
        num_speakers: int | None = None,
        threshold: float = 0.5,
        min_duration_on: float = 0.3,
        min_duration_off: float = 0.5,
        num_threads: int = 2,
        device: str = "cpu",
        _diarizer: Any = None,
    ):
        self._init_common()
        self.seg = Path(segmentation_model) if segmentation_model else MODELS_DIR / SHERPA_SEG
        self.emb = Path(embedding_model) if embedding_model else MODELS_DIR / SHERPA_EMB
        self.name = f"sherpa-onnx:{self.seg.parent.name}+{self.emb.stem}"
        self.num_speakers = num_speakers if num_speakers and num_speakers > 0 else -1
        self.threshold = threshold
        self.min_on, self.min_off = min_duration_on, min_duration_off
        self.num_threads = num_threads
        self.provider = "cuda" if device == "cuda" else "cpu"  # 既定は CPU（GPU は STT に譲る）
        self._diarizer = _diarizer

    def _load(self):
        if self._diarizer is not None:
            return self._diarizer
        import sherpa_onnx as so  # noqa: PLC0415

        for p in (self.seg, self.emb):
            if not p.exists():
                raise RuntimeError(f"モデルが無い: {p}（python -m intake models で取得する）")
        cfg = so.OfflineSpeakerDiarizationConfig(
            segmentation=so.OfflineSpeakerSegmentationModelConfig(
                pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(self.seg)),
                num_threads=self.num_threads, provider=self.provider),
            embedding=so.SpeakerEmbeddingExtractorConfig(model=str(self.emb), num_threads=self.num_threads, provider=self.provider),
            clustering=so.FastClusteringConfig(num_clusters=self.num_speakers, threshold=self.threshold),
            min_duration_on=self.min_on,
            min_duration_off=self.min_off,
        )
        if not cfg.validate():
            raise RuntimeError("sherpa-onnx の話者分離の設定が不正")
        self._diarizer = so.OfflineSpeakerDiarization(cfg)
        return self._diarizer

    def diarize(self, audio: np.ndarray, sr: int) -> list[tuple[float, float, str]]:
        d = self._load()
        if sr != getattr(d, "sample_rate", sr):
            raise ValueError(f"話者分離は {d.sample_rate} Hz の音声が要る: {sr}")
        res = d.process(np.ascontiguousarray(audio, dtype=np.float32)).sort_by_start_time()
        return [(float(r.start), float(r.end), f"S{r.speaker}") for r in res]


class PyannoteSpeaker(AcousticSpeaker):
    def __init__(
        self,
        model: str = "pyannote/speaker-diarization-community-1",
        device: str = "auto",
        num_speakers: int | None = None,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
        token_env: str = "HF_TOKEN",
        exclusive: bool = True,
        _pipeline: Any = None,  # テスト用の差し込み口
    ):
        self.model = model
        self.name = f"pyannote-{model.rstrip('/').split('/')[-1]}"
        self.device = device
        self.kw = {k: v for k, v in
                   {"num_speakers": num_speakers, "min_speakers": min_speakers, "max_speakers": max_speakers}.items()
                   if v is not None}
        self.token_env = token_env
        self.exclusive = exclusive
        self._pipeline = _pipeline
        self._init_common()

    def _load(self):
        if self._pipeline is not None:
            return self._pipeline
        import torch  # noqa: PLC0415
        from pyannote.audio import Pipeline  # noqa: PLC0415

        token = os.environ.get(self.token_env) or None
        try:
            pipe = Pipeline.from_pretrained(self.model, token=token)
        except TypeError:  # pyannote 3.x
            pipe = Pipeline.from_pretrained(self.model, use_auth_token=token)
        if pipe is None:
            raise RuntimeError(f"{self.model} を読めない（HuggingFace で利用条件に同意し、{self.token_env} を設定する）")
        dev = self.device
        if dev == "auto":
            dev = "cuda" if torch.cuda.is_available() else "cpu"
        pipe.to(torch.device(dev))
        self._pipeline = pipe
        return pipe

    def diarize(self, audio: np.ndarray, sr: int) -> list[tuple[float, float, str]]:
        """音声全体を話者分離し、(t0, t1, 元のラベル) の列を返す。単独で呼べる関数境界。"""
        pipe = self._load()
        wav: Any = audio.astype(np.float32)[None, :]
        try:
            import torch  # noqa: PLC0415

            wav = torch.from_numpy(wav)
        except ImportError:  # テスト用の差し込み口では torch 無しでも通す
            pass
        out = pipe({"waveform": wav, "sample_rate": sr}, **self.kw)
        ann = out
        if hasattr(out, "speaker_diarization"):  # pyannote 4.x の DiarizeOutput
            ann = out.exclusive_speaker_diarization if self.exclusive else out.speaker_diarization
        return [(float(seg.start), float(seg.end), str(lbl)) for seg, _, lbl in ann.itertracks(yield_label=True)]
