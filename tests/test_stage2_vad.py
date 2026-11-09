import json
from pathlib import Path

import pytest

from intake.config import load_config
from intake.pipeline import Pipeline
from intake.replay import WavReplay, import_audio
from intake.sink import JsonlSink

pytest.importorskip("onnxruntime")
SAMPLE = Path("samples/conv2_ja_espeak.wav")


@pytest.mark.skipif(not SAMPLE.exists(), reason="samples/conv2_ja_espeak.wav が無い")
def test_silero_segments_cover_script(store):
    sid = import_audio(store, SAMPLE, label="vad")
    cfg = load_config("config/silero.yaml")
    p = Pipeline(cfg, sid, JsonlSink(store.run_dir(sid, "t")), run_name="t")
    stats = p.run(WavReplay(store, sid).chunks(), "replay:fast")
    segs = [json.loads(x) for x in (store.run_dir(sid, "t") / "trace" / "vad.jsonl").read_text().splitlines()]
    assert stats["vad_segments"] == len(segs) > 0
    for a, b in zip(segs, segs[1:]):
        assert a["t1"] <= b["t0"] and a["t0"] < a["t1"]
    assert all(s["t1"] - s["t0"] <= cfg["vad"]["max_speech_s"] + 0.5 for s in segs)
    script = json.loads(SAMPLE.with_suffix(".script.json").read_text(encoding="utf-8"))
    for line in script["lines"]:
        mid = (line["t0"] + line["t1"]) / 2
        assert any(s["t0"] <= mid <= s["t1"] for s in segs) or any(
            s["t0"] <= line["t0"] + 1 <= s["t1"] for s in segs
        ), line


def test_silero_silence_gives_nothing(store, tmp_path):
    import numpy as np
    import soundfile as sf

    f = tmp_path / "z.wav"
    sf.write(f, np.zeros(16000 * 3, dtype=np.float32), 16000)
    sid = import_audio(store, f, label="zero")
    cfg = load_config("config/silero.yaml")
    p = Pipeline(cfg, sid, JsonlSink(store.run_dir(sid, "t")), run_name="t")
    assert p.run(WavReplay(store, sid).chunks(), "replay:fast")["vad_segments"] == 0


def test_set_override():
    cfg = load_config("config/silero.yaml", ["vad.min_silence_ms=800", "replay.speed=realtime"])
    assert cfg["vad"]["min_silence_ms"] == 800 and cfg["replay"]["speed"] == "realtime"
