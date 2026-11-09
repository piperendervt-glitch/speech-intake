import json
import threading

import numpy as np
import pytest
import soundfile as sf

from intake.__main__ import main
from intake.capture import MicCapture
from intake.config import check_local_only, load_config
from intake.replay import WavReplay, import_audio

from .conftest import tone_bursts


class FakeStream:
    """sounddevice.InputStream の代わり。別スレッドから callback にブロックを渡す。"""

    def __init__(self, samplerate, channels, dtype, blocksize, device, callback, signal):
        self.cb, self.bs, self.sig = callback, blocksize, signal
        self.th = threading.Thread(target=self._run, daemon=True)
        self.done = threading.Event()

    def _run(self):
        for i in range(0, len(self.sig), self.bs):
            self.cb(self.sig[i : i + self.bs, None], self.bs, None, None)
        self.done.set()

    def __enter__(self):
        self.th.start()
        return self

    def __exit__(self, *a):
        self.th.join()


def test_capture_writes_wav_and_times_from_samples(store):
    sig = tone_bursts()
    streams = []

    def factory(**kw):
        s = FakeStream(signal=sig, **kw)
        streams.append(s)
        return s

    cap = MicCapture(store, "20261109_160000", stream_factory=factory)
    got = []
    for ch in cap.chunks():
        got.append(ch)
        if streams and streams[0].done.is_set() and cap._q.empty():
            cap.stop()
    total = sum(len(c.samples) for c in got)
    assert total == len(sig)
    # 時刻は書いたサンプル数だけで決まる
    assert got[10].t0 == pytest.approx(10 * cap.blocksize / 16000)
    data, sr = sf.read(store.audio_path("20261109_160000"), dtype="float32")
    assert sr == 16000 and len(data) == len(sig)
    assert np.max(np.abs(data - sig)) < 1e-3
    meta = store.read_meta("20261109_160000")
    assert meta["status"] == "done" and meta["duration"] == pytest.approx(4.0)


def test_replay_fast_and_realtime(store, wav_file):
    sid = import_audio(store, wav_file, label="t")
    chunks = list(WavReplay(store, sid, speed="fast").chunks())
    assert chunks[0].t0 == 0 and chunks[-1].t1 == pytest.approx(4.0)
    import time

    rp = WavReplay(store, sid, speed="realtime", chunk_ms=100)
    t = time.monotonic()
    for c in rp.chunks():
        if c.t0 >= 0.5:
            break
    assert time.monotonic() - t >= 0.45


def test_cli_import_replay_writes_run(tmp_path, wav_file, capsys):
    cfgp = tmp_path / "c.yaml"
    cfgp.write_text(f"sessions_dir: {tmp_path / 's'}\n", encoding="utf-8")
    main(["-c", str(cfgp), "import", str(wav_file), "--label", "x", "--replay", "--run", "r1"])
    out = capsys.readouterr().out.strip().splitlines()
    sid = out[0]
    stats = json.loads(out[-1])
    assert stats["audio_s"] == pytest.approx(4.0)
    rd = tmp_path / "s" / sid / "runs" / "r1"
    assert (rd / "utterances.jsonl").exists()
    info = json.loads((rd / "run.json").read_text(encoding="utf-8"))
    assert info["providers"] == {"vad": "none", "stt": "none", "speaker": "none"}


def test_non_local_host_refused():
    c = load_config(None)
    c["viewer"]["host"] = "0.0.0.0"
    with pytest.raises(ValueError):
        check_local_only(c)
