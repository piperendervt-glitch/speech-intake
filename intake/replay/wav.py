from __future__ import annotations

import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Iterator

import numpy as np
import soundfile as sf

from ..audio import AudioChunk
from ..session import SessionStore, new_session_id


class WavReplay:
    """sessions/<sid>/audio.wav を AudioChunk の列として流す。

    speed="realtime" は音声の長さどおりに待ちながら流す（ライブと同じ条件で各段を試すため）。
    マイクと同じく、チャンクの終わりの時刻になってから渡す（段10。段1〜9 はチャンクの先頭の時刻に渡していて、
    chunk_ms だけ早く届いていた）。
    speed="fast" は待たずに流す。
    """

    def __init__(self, store: SessionStore, session: str, speed: str = "fast", chunk_ms: int = 32):
        if speed not in ("realtime", "fast"):
            raise ValueError(f"speed は realtime か fast: {speed!r}")
        self.store = store
        self.session = session
        self.speed = speed
        self.path = store.audio_path(session)
        info = sf.info(str(self.path))
        if info.channels != 1:
            raise ValueError(f"mono のみ対応: {self.path} は {info.channels}ch")
        self.sample_rate = info.samplerate
        self.duration = info.frames / info.samplerate
        self.blocksize = int(self.sample_rate * chunk_ms / 1000)

    def chunks(self) -> Iterator[AudioChunk]:
        start = time.monotonic()
        pos = 0
        with sf.SoundFile(str(self.path)) as f:
            for block in f.blocks(blocksize=self.blocksize, dtype="float32", always_2d=False):
                t0 = pos / self.sample_rate
                pos += len(block)
                if self.speed == "realtime":
                    wait = start + pos / self.sample_rate - time.monotonic()
                    if wait > 0:
                        time.sleep(wait)
                yield AudioChunk(t0=t0, samples=np.ascontiguousarray(block, dtype=np.float32), sample_rate=self.sample_rate)


def import_audio(store: SessionStore, src: str | Path, sample_rate: int = 16000, label: str | None = None) -> str:
    """外部の音声ファイルを 16 kHz mono PCM16 に変換して新しいセッションにする。

    ffmpeg があればそれで変換（任意の形式・高品質リサンプル）、無ければ soundfile で読み、
    同じサンプルレートの mono/stereo WAV だけを受け付ける。
    """
    src = Path(src)
    if not src.exists():
        raise FileNotFoundError(src)
    sid = new_session_id(datetime.fromtimestamp(src.stat().st_mtime), suffix=label)
    n = 1
    while store.dir(sid).exists():
        n += 1
        sid = new_session_id(datetime.fromtimestamp(src.stat().st_mtime), suffix=f"{label or 'imp'}-{n}")
    store.create(sid, {"source": "import", "original": src.name, "sample_rate": sample_rate, "status": "importing"})
    dst = store.audio_path(sid)
    if shutil.which("ffmpeg"):
        subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src),
             "-ac", "1", "-ar", str(sample_rate), "-sample_fmt", "s16", str(dst)],
            check=True,
        )
    else:
        data, sr = sf.read(str(src), dtype="float32", always_2d=True)  # libsndfile 1.1 以降は mp3 も読める
        x = data.mean(axis=1)
        if sr != sample_rate:
            try:
                from math import gcd  # noqa: PLC0415

                from scipy.signal import resample_poly  # noqa: PLC0415
            except ImportError:
                raise RuntimeError(f"ffmpeg も scipy も無いためリサンプルできない（{sr} Hz → {sample_rate} Hz）") from None
            g = gcd(sr, sample_rate)
            x = resample_poly(x, sample_rate // g, sr // g).astype("float32")
        sf.write(str(dst), x, sample_rate, subtype="PCM_16")
    info = sf.info(str(dst))
    store.update_meta(sid, status="done", duration=round(info.frames / info.samplerate, 3))
    return sid
