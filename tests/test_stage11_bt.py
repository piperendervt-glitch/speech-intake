"""段11：Bluetooth ヘッドセットの入力（機器の指定・レート変換・機器が消えたとき・診断・遅れの実測）。"""

import json
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from intake.capture import MicCapture, resolve_device
from intake.devcheck import analyze, analyze_session
from intake.resample import StreamResampler, resample

ROOT = Path(__file__).resolve().parents[1]
EN = ROOT / "samples" / "en_read_kokoro.wav"
sys.path.insert(0, str(ROOT / "tools"))

DEVS = [
    {"index": 1, "name": "マイク配列 (Realtek(R) Audio)", "hostapi": "MME"},
    {"index": 3, "name": "ヘッドセット (AirPods Hands-Free)", "hostapi": "MME"},
    {"index": 9, "name": "ヘッドセット (AirPods Hands-Free)", "hostapi": "Windows WASAPI"},
    {"index": 12, "name": "Headset (@System32\\drivers\\bthhfenum.sys,#2;%1 Hands-Free%0\n;(AirPods))", "hostapi": "Windows WDM-KS"},
]


def test_resolve_device_by_name():
    assert resolve_device(None, DEVS) is None
    assert resolve_device(3, DEVS) == 3
    assert resolve_device("9", DEVS) == 9
    assert resolve_device("realtek", DEVS) == 1
    assert resolve_device("AirPods@MME", DEVS) == 3
    assert resolve_device("airpods", DEVS, hostapi="wasapi") == 9
    with pytest.raises(ValueError, match="3 つ") as e:
        resolve_device("AirPods", DEVS)
    assert "Windows WASAPI" in str(e.value) and "@MME" in str(e.value)  # 候補と絞り方を出す
    with pytest.raises(ValueError, match="無い"):
        resolve_device("Jabra", DEVS)
    with pytest.raises(ValueError, match="番号"):
        resolve_device(7, DEVS)


@pytest.mark.parametrize("a,b", [(48000, 16000), (44100, 16000), (8000, 16000)])
def test_stream_resampler_matches_batch_and_tone(a, b):
    t = np.arange(a) / a
    x = np.sin(2 * np.pi * 1000 * t).astype(np.float32)
    y1 = resample(x, a, b)
    r = StreamResampler(a, b)
    parts, i = [], 0
    for k in [1, 7, 500, 3, 1024, 2048] * 50:
        if i >= len(x):
            break
        parts.append(r.process(x[i:i + k]))
        i += k
    parts.append(r.process(x[i:]))
    parts.append(r.flush())
    y2 = np.concatenate(parts)
    assert len(y1) == len(y2) == b  # 1 秒 → 1 秒
    assert np.max(np.abs(y1 - y2)) < 1e-6  # ブロックの切れ目で変わらない
    ref = np.sin(2 * np.pi * 1000 * np.arange(b) / b)
    assert np.max(np.abs(y1[100:-100] - ref[100:-100])) < 1e-3


def test_resampler_removes_alias():
    t = np.arange(48000) / 48000
    y = resample(np.sin(2 * np.pi * 10000 * t), 48000, 16000)  # 10 kHz は 16 kHz では出せない → 消える
    assert np.sqrt(np.mean(y[200:-200] ** 2)) < 1e-3


class FakeStream:
    """sounddevice.InputStream の代わり。stop_after 秒分を渡したら黙る（機器が消えたとき）。"""

    def __init__(self, samplerate, channels, dtype, blocksize, device, callback, sig, fail_rates=(), **kw):
        if samplerate in fail_rates:
            raise RuntimeError("Invalid sample rate [PaErrorCode -9997]")
        self.samplerate, self.cb, self.bs, self.sig = samplerate, callback, blocksize, sig
        self.done = threading.Event()
        self.th = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        for i in range(0, len(self.sig), self.bs):
            self.cb(self.sig[i:i + self.bs, None], self.bs, None, None)
        self.done.set()

    def __enter__(self):
        self.th.start()
        return self

    def __exit__(self, *a):
        self.th.join()


def _tone(sr, s):
    t = np.arange(int(sr * s)) / sr
    return (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def test_mic_native_rate_is_resampled_and_recorded(store):
    sig = _tone(48000, 1.0)
    streams = []

    def factory(**kw):
        s = FakeStream(sig=sig, **kw)
        streams.append(s)
        return s

    cap = MicCapture(store, "20261110_120000", stream_factory=factory, device_rate=48000)
    total = 0
    for ch in cap.chunks():
        total += len(ch.samples)
        if streams[0].done.is_set() and cap._q.empty():
            cap.stop()
    assert streams[0].samplerate == 48000
    meta = store.read_meta("20261110_120000")
    assert meta["device_rate"] == 48000 and meta["resampled"] is True and meta["sample_rate"] == 16000
    x, sr = sf.read(store.audio_path("20261110_120000"), dtype="float32")
    assert sr == 16000 and abs(len(x) - 16000) <= 2  # 1 秒 → 16000 サンプル
    ref = 0.3 * np.sin(2 * np.pi * 440 * np.arange(len(x)) / 16000)
    assert np.max(np.abs(x[200:-200] - ref[200:-200])) < 2e-3


def test_mic_falls_back_to_native_when_16k_refused(store, monkeypatch):
    sig = _tone(44100, 0.5)
    streams = []

    def factory(**kw):
        s = FakeStream(sig=sig, fail_rates=(16000,), **kw)  # WASAPI の共有モードのように 16 kHz を断る
        streams.append(s)
        return s

    cap = MicCapture(store, "20261110_120001", stream_factory=factory)
    monkeypatch.setattr(cap, "device_info", lambda: {"device_name": "AirPods", "hostapi": "Windows WASAPI", "device_default_rate": 44100.0})
    for _ in cap.chunks():
        if streams and streams[-1].done.is_set() and cap._q.empty():
            cap.stop()
    m = store.read_meta("20261110_120001")
    assert m["device_rate"] == 44100 and m["resampled"] and m["device_name"] == "AirPods" and m["device_default_rate"] == 44100.0


def test_mic_device_lost_saves_and_stops(store):
    sig = _tone(16000, 0.6)
    cap = MicCapture(store, "20261110_120002", stream_factory=lambda **kw: FakeStream(sig=sig, **kw), stall_s=0.3)
    t = time.monotonic()
    n = sum(len(c.samples) for c in cap.chunks())  # stop() を呼ばない：音が止まったら自分で終わる
    assert time.monotonic() - t < 5
    assert n == len(sig)
    m = store.read_meta("20261110_120002")
    assert m["ended_by"] == "device_lost" and "届かなく" in m["lost"] and m["status"] == "done"
    assert m["duration"] == pytest.approx(0.6, abs=0.01)
    assert sf.info(str(store.audio_path("20261110_120002"))).frames == len(sig)  # それまでの録音は残る


def _speech(narrow: bool, noise_db=-60.0, head=0.5, drop_at=None):
    x, sr = sf.read(EN, dtype="float32")
    rng = np.random.default_rng(1)
    y = 0.8 * x + (10 ** (noise_db / 20)) * rng.standard_normal(len(x)).astype(np.float32)
    if narrow:  # 8 kHz 相当（HFP の狭帯域）を 16 kHz で申告したもの
        y = resample(resample(y, 16000, 8000), 8000, 16000)[: len(x)]
    y = np.concatenate([np.zeros(int(head * sr), np.float32), y])
    if drop_at is not None:
        a = int(drop_at * sr)
        y[a:a + int(0.1 * sr)] = 0
    return y


@pytest.mark.skipif(not EN.exists(), reason="samples/en_read_kokoro.wav が無い")
def test_devcheck_wide_and_narrow():
    w = analyze(_speech(False, drop_at=6.0))
    n = analyze(_speech(True))
    assert w["verdict"] == "wide" and w["verdict_text"] == "広帯域らしい"
    assert n["verdict"] == "narrow" and n["verdict_text"] == "狭帯域らしい"
    assert w["band"]["upper_hz"] > 6000 and n["band"]["upper_hz"] < 4400
    assert n["band"]["sibilant_frames"] == 0 < w["band"]["sibilant_frames"]
    assert w["head_silence_ms"] == pytest.approx(500, abs=20)
    assert w["dropout_count"] == 1 and w["dropouts"][0]["t"] == pytest.approx(6.0, abs=0.02)
    assert w["noise_dbfs"] == pytest.approx(-62, abs=4)
    assert set(w) >= {"spectrum_db", "speech_dbfs", "snr_db"}


def test_devcheck_unclear_when_no_voice(store):
    assert analyze(np.zeros(16000 * 3, np.float32))["verdict"] == "unclear"
    rng = np.random.default_rng(0)
    r = analyze((0.01 * rng.standard_normal(16000 * 3)).astype(np.float32))  # 雑音だけ
    assert r["verdict"] == "unclear"


@pytest.mark.skipif(not EN.exists(), reason="samples/en_read_kokoro.wav が無い")
def test_devcheck_session_writes_json(store):
    sid = "20261110_120003_devcheck"
    store.create(sid, {"source": "mic", "device_name": "AirPods", "device_rate": 16000, "device_default_rate": 16000.0})
    sf.write(store.audio_path(sid), _speech(True), 16000, subtype="PCM_16")
    rep = analyze_session(store, sid)
    saved = json.loads((store.dir(sid) / "devcheck.json").read_text(encoding="utf-8"))
    assert saved["verdict"] == rep["verdict"] == "narrow" and saved["device"]["device_name"] == "AirPods"


def _claps(sr, times, total, delay=0.0, seed=0):
    rng = np.random.default_rng(seed)
    x = (0.002 * rng.standard_normal(int(sr * total))).astype(np.float32)
    for t in times:
        a = int((t + delay) * sr)
        n = int(0.03 * sr)
        x[a:a + n] += (0.6 * rng.standard_normal(n) * np.exp(-np.arange(n) / (0.005 * sr))).astype(np.float32)
    return x


def test_latency_pair_measures_delay(store):
    import latency_pair as lp

    sr = 16000
    times = [1.0, 3.1, 5.3, 7.2]
    # B は 0.4 秒遅れて録音が始まり（壁時計の差）、さらに 180 ms 遅れて届く
    xa = _claps(sr, times, 9.0, seed=1)
    xb = _claps(sr, times, 9.0, delay=-0.4 + 0.18, seed=2)
    sa, sb = "20261110_130000_pair-a", "20261110_130000_pair-b"
    store.create(sa, {"source": "mic", "t0_unix": 1000.0})
    store.create(sb, {"source": "mic", "t0_unix": 1000.4})
    sf.write(store.audio_path(sa), xa, sr, subtype="PCM_16")
    sf.write(store.audio_path(sb), xb, sr, subtype="PCM_16")
    res = lp.analyze(store, sa, sb)
    assert res["wall_offset_s"] == pytest.approx(-0.4)
    assert res["lag_ms"] == pytest.approx(180, abs=3)
    assert len(res["claps"]) == 4 and all(abs(c["lag_ms"] - 180) <= 3 for c in res["claps"])
    assert store.read_meta(sa)["pair"]["with"] == sb and store.read_meta(sb)["pair"]["role"] == "b"
    assert "capture.latency_ms: 18" in lp.report(res, store.read_meta(sa), store.read_meta(sb))


def test_t_said_unix_uses_latency_ms(cfg, monkeypatch):
    from intake.audio import AudioChunk
    from intake.pipeline import Pipeline
    from intake.sink import MultiSink

    class Rec:
        def __init__(self):
            self.msgs = []

        def start(self, info): pass
        def audio(self, c): pass
        def emit(self, u, **kw): pass
        def trace(self, *a): pass
        def end(self, s): pass
        def close(self): pass

        def event(self, m):
            self.msgs.append(m)

    def run(lat, source):
        c = {**cfg, "vad": {"provider": "silero", "threshold": 0.5, "min_silence_ms": 200, "start_frames": 1},
             "capture": {**cfg["capture"], "latency_ms": lat}}
        rec = Rec()
        p = Pipeline(c, "20261110_140000", MultiSink([rec]), run_name="t")
        x, _ = sf.read(EN, dtype="float32", frames=16000 * 4)

        def chunks():  # 実時間で届いたことにする：チャンクの終わりから 50 ms 後に届く時計
            for i in range(0, len(x), 512):
                c = AudioChunk(t0=i / 16000, samples=x[i:i + 512])
                now[0] = 1000.0 + c.t1 + 0.05
                yield c

        p.run(chunks(), source)
        return [m for m in rec.msgs if m["type"] in ("speech_start", "speech_end")], p._clock0

    if not EN.exists():
        pytest.skip("samples が無い")
    import intake.pipeline as pl

    now = [1000.0]
    monkeypatch.setattr(pl.time, "time", lambda: now[0])
    ev, clock0 = run(150, "mic")
    assert clock0 == pytest.approx(1000.05)
    st = next(m for m in ev if m["type"] == "speech_start")
    assert st["t_said_unix"] == pytest.approx(clock0 + st["t"] - 0.15, abs=2e-3)
    en = next(m for m in ev if m["type"] == "speech_end")
    assert en["t_said_unix"] == pytest.approx(clock0 + en["t1"] - 0.15, abs=2e-3)
    ev2, _ = run(150, "replay:fast")  # 流し直しには話した時刻が無い
    assert all("t_said_unix" not in m for m in ev2)
