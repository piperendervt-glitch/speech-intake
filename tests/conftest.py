import numpy as np
import pytest
import soundfile as sf

from intake.config import load_config
from intake.session import SessionStore


def tone_bursts(sr=16000, spec=((0.5, 1.5), (2.0, 3.2)), total=4.0):
    """無音の中に 220 Hz のバーストを置いたテスト信号。"""
    t = np.arange(int(sr * total)) / sr
    x = np.zeros_like(t, dtype=np.float32)
    for a, b in spec:
        m = (t >= a) & (t < b)
        x[m] = 0.3 * np.sin(2 * np.pi * 220 * t[m])
    return x


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions")


@pytest.fixture
def cfg(tmp_path):
    c = load_config(None)
    c["sessions_dir"] = str(tmp_path / "sessions")
    return c


@pytest.fixture
def wav_file(tmp_path):
    p = tmp_path / "in.wav"
    sf.write(p, tone_bursts(), 16000, subtype="PCM_16")
    return p
