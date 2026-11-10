from __future__ import annotations

import queue
import threading
import time
from datetime import datetime
from typing import Any, Iterator

import numpy as np
import soundfile as sf

from ..audio import AudioChunk
from ..session import SessionStore


def _sd():
    try:
        import sounddevice as sd  # noqa: PLC0415
    except OSError as e:  # PortAudio が無い環境
        raise RuntimeError(f"sounddevice を読み込めない（PortAudio が必要）: {e}") from e
    return sd


def list_input_devices() -> list[dict[str, Any]]:
    sd = _sd()
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            out.append({"index": i, "name": d["name"], "default_samplerate": d["default_samplerate"]})
    return out


class MicCapture:
    """マイクから録音し、sessions/<sid>/audio.wav に書きつつチャンクを返す。

    時刻は「書いたサンプル数 / sample_rate」だけで決める（壁時計は使わない）。
    stream_factory はテスト用の差し込み口（sounddevice.InputStream と同じ引数を受ける）。
    block_ms  1 回に受け取る音声の長さ。短いほど VAD に早く届く（段10。VAD の枠は 32 ms なので、それより短くしても
              確定が早まるのは枠の切れ目との差の分だけ）
    latency   PortAudio に頼む入力の遅れ（"low" / "high" / 秒。None は sounddevice の既定 = "high"）
    """

    def __init__(
        self,
        store: SessionStore,
        session: str,
        sample_rate: int = 16000,
        device: int | str | None = None,
        block_ms: int = 32,
        latency: str | float | None = None,
        stream_factory=None,
        flush_every_s: float = 1.0,
    ):
        self.store = store
        self.session = session
        self.sample_rate = sample_rate
        self.device = device
        self.blocksize = int(sample_rate * block_ms / 1000)
        self.latency = latency
        self._stream_factory = stream_factory
        self._q: queue.Queue[np.ndarray | None] = queue.Queue()
        self._stop = threading.Event()
        self._flush_every = flush_every_s
        self.overflows = 0
        self.samples_written = 0
        self.t0_unix: float | None = None
        self._prepared = False

    def stop(self) -> None:
        self._stop.set()

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ARG002
        if status and getattr(status, "input_overflow", False):
            self.overflows += 1
        if self.t0_unix is None:
            # 最初のブロックの先頭サンプルの壁時計（概算。外部プロセスの時刻をセッション秒に直す手がかり）
            self.t0_unix = time.time() - frames / self.sample_rate
        self._q.put(np.array(indata[:, 0], dtype=np.float32, copy=True))

    def prepare(self) -> None:
        """セッションを作る。run の出力（runs/<run>/）を置く前に呼ぶ（record はこの順。段10 で直した）。"""
        if self._prepared:
            return
        self.store.create(
            self.session,
            {
                "source": "mic",
                "device": self.device,
                "sample_rate": self.sample_rate,
                "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "status": "recording",
            },
        )
        self._prepared = True

    def chunks(self) -> Iterator[AudioChunk]:
        factory = self._stream_factory or _sd().InputStream
        self.prepare()
        path = self.store.audio_path(self.session)
        last_flush = time.monotonic()
        with sf.SoundFile(path, "w", samplerate=self.sample_rate, channels=1, subtype="PCM_16") as wav:
            stream = factory(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=self.blocksize,
                device=self.device,
                callback=self._callback,
                **({"latency": self.latency} if self.latency is not None else {}),
            )
            try:
                with stream:
                    lat = getattr(stream, "latency", None)  # PortAudio が見積もった入力の遅れ（秒）
                    if isinstance(lat, (int, float)):
                        self.store.update_meta(self.session, input_latency_s=round(float(lat), 4), block_ms=round(self.blocksize / self.sample_rate * 1000, 1))
                    while not self._stop.is_set():
                        try:
                            block = self._q.get(timeout=0.1)
                        except queue.Empty:
                            continue
                        t0 = self.samples_written / self.sample_rate
                        if self.samples_written == 0 and self.t0_unix is not None:
                            self.store.update_meta(self.session, t0_unix=round(self.t0_unix, 4))
                        wav.write(block)
                        self.samples_written += len(block)
                        if time.monotonic() - last_flush > self._flush_every:
                            wav.flush()  # ヘッダを更新し、録音中でも閲覧画面から読めるようにする
                            last_flush = time.monotonic()
                        yield AudioChunk(t0=t0, samples=block, sample_rate=self.sample_rate)
                    # 止めた後に残ったブロックも保存する
                    while not self._q.empty():
                        block = self._q.get_nowait()
                        if block is None:
                            break
                        t0 = self.samples_written / self.sample_rate
                        wav.write(block)
                        self.samples_written += len(block)
                        yield AudioChunk(t0=t0, samples=block, sample_rate=self.sample_rate)
            finally:
                self.store.update_meta(
                    self.session,
                    status="done",
                    duration=round(self.samples_written / self.sample_rate, 3),
                    overflows=self.overflows,
                )
