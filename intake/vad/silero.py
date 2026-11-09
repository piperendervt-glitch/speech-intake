"""Silero VAD（ONNX 版を onnxruntime で動かす。torch は不要）。

モデルファイルは silero-vad 6.2.3（MIT, https://github.com/snakers4/silero-vad）の silero_vad.onnx を
intake/vad/models/ に同梱している（PyPI の silero-vad パッケージは torch を要求するため、ファイルだけ持ってきた）。
model_path を設定すれば別のファイルを使える。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..audio import AudioChunk
from ..schema import VadSegment

FRAME = 512  # 16 kHz で 32 ms。Silero v5 以降の固定長
CONTEXT = 64


MODEL = Path(__file__).parent / "models" / "silero_vad_v6.2.3.onnx"
MODEL_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"


class SileroVad:
    """チャンクを 512 サンプルの枠に切ってモデルに通し、確率から発話区間を決める。

    threshold       この確率以上で発話の始まり
    neg_threshold   発話中、この確率未満が min_silence_ms 続いたら終わり（既定 threshold-0.15）
    min_speech_ms   これより短い区間は捨てる
    min_silence_ms  区間を閉じるのに要る無音の長さ（短いと 1 文が細切れ、長いと話者交代を跨ぐ）
    speech_pad_ms   区間の前後に足す余白（STT が語頭・語尾を落とさないように）
    max_speech_s    これを超えたら区間を切る（Whisper の 30 秒窓に収めるため）
    keep_prob       枠ごとの確率を trace/vad_prob.jsonl に書く（閲覧画面で確率の曲線を出す）
    """

    def __init__(
        self,
        threshold: float = 0.5,
        neg_threshold: float | None = None,
        min_speech_ms: int = 250,
        min_silence_ms: int = 300,
        speech_pad_ms: int = 100,
        max_speech_s: float = 28.0,
        model_path: str | None = None,
        keep_prob: bool = True,
        num_threads: int = 1,
    ):
        import onnxruntime as ort  # noqa: PLC0415

        path = model_path or str(MODEL)
        self.name = "silero-v6" if not model_path else f"silero-custom:{Path(model_path).name}"
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = num_threads
        self.sess = ort.InferenceSession(path, sess_options=opts, providers=["CPUExecutionProvider"])
        self.thr = threshold
        self.neg = neg_threshold if neg_threshold is not None else max(threshold - 0.15, 0.01)
        self.min_speech = min_speech_ms / 1000
        self.min_silence = min_silence_ms / 1000
        self.pad = speech_pad_ms / 1000
        self.max_speech = max_speech_s
        self.keep_prob = keep_prob
        self.sr = 16000
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._ctx = np.zeros(CONTEXT, dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)
        self._pos = 0  # 処理済みサンプル数
        self._start: float | None = None
        self._temp_end: float | None = None
        self._probs: list[float] = []
        self._prev_end = 0.0
        self._trace: list[tuple[str, dict]] = []

    def prob(self, frame: np.ndarray) -> float:
        x = np.concatenate([self._ctx, frame])[None, :].astype(np.float32)
        out, self._state = self.sess.run(None, {"input": x, "state": self._state, "sr": np.array(self.sr, dtype=np.int64)})
        self._ctx = x[0, -CONTEXT:]
        return float(out[0, 0])

    def _close(self, end: float, out: list[VadSegment]) -> None:
        start = max(self._start - self.pad, self._prev_end, 0.0)
        end = end + self.pad
        if end - start - 2 * self.pad >= self.min_speech:
            p = float(np.mean(self._probs)) if self._probs else None
            out.append(VadSegment(t0=start, t1=end, vad=self.name, p=p))
            self._prev_end = end
        self._start = None
        self._temp_end = None
        self._probs = []

    def feed(self, chunk: AudioChunk) -> list[VadSegment]:
        if chunk.sample_rate != self.sr:
            raise ValueError(f"Silero は 16 kHz のみ: {chunk.sample_rate}")
        buf = np.concatenate([self._pending, chunk.samples]) if len(self._pending) else chunk.samples
        out: list[VadSegment] = []
        i = 0
        while i + FRAME <= len(buf):
            frame = buf[i : i + FRAME]
            t = self._pos / self.sr  # 枠の先頭
            p = self.prob(frame)
            self._pos += FRAME
            i += FRAME
            if self.keep_prob:
                self._trace.append(("vad_prob", {"t": round(t, 3), "p": round(p, 3)}))
            if self._start is None:
                if p >= self.thr:
                    self._start = t
                    self._probs = [p]
                continue
            self._probs.append(p)
            if p >= self.thr:
                self._temp_end = None
            elif p < self.neg:
                if self._temp_end is None:
                    self._temp_end = t
                if t + FRAME / self.sr - self._temp_end >= self.min_silence:
                    self._close(self._temp_end, out)
                    continue
            if t + FRAME / self.sr - self._start >= self.max_speech:
                cut = self._temp_end if self._temp_end is not None else t + FRAME / self.sr
                self._close(cut - self.pad, out)  # 切れ目に余白は足さない
                if p >= self.thr:
                    self._start, self._probs = t, [p]
        self._pending = buf[i:].copy()
        return out

    def flush(self) -> list[VadSegment]:
        out: list[VadSegment] = []
        if self._start is not None:
            end = self._temp_end if self._temp_end is not None else self._pos / self.sr
            self._close(end, out)
            # 余白が音声の終わりを越えないように
            if out:
                out[-1].t1 = min(out[-1].t1, (self._pos + len(self._pending)) / self.sr)
        return out

    def drain_trace(self) -> list[tuple[str, dict]]:
        t, self._trace = self._trace, []
        return t
