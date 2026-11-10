"""段10：ライブ会話向けの即時通知（speech_start / speech_end / speech_dropped と utterance の seg・timing）。"""

import asyncio
import json
import socket
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from intake.audio import AudioChunk
from intake.config import load_config
from intake.pipeline import Pipeline
from intake.replay import WavReplay, import_audio
from intake.schema import Utterance
from intake.sink import JsonlSink, MultiSink
from intake.stt.base import SttResult

pytest.importorskip("onnxruntime")
from intake.vad.silero import FRAME, SileroVad  # noqa: E402

SAMPLE = Path("samples/conv2_ja_kokoro.wav")
F = FRAME / 16000  # 1 枠の秒


def scripted(probs, **kw):
    """枠ごとの確率を台本どおりに返す SileroVad（状態の移り変わりだけを試す）。"""
    v = SileroVad(**kw)
    it = iter(probs)
    v.prob = lambda frame: next(it)  # noqa: ARG005
    return v


def feed_all(v, n_frames, chunk_frames=3):
    segs = []
    x = np.zeros(n_frames * FRAME, dtype=np.float32)
    step = chunk_frames * FRAME
    for i in range(0, len(x), step):
        segs += v.feed(AudioChunk(t0=i / 16000, samples=x[i : i + step], sample_rate=16000))
    segs += v.flush()
    return segs


def test_vad_events_start_end_and_seg():
    probs = [0.0] * 5 + [0.9] * 20 + [0.0] * 15
    v = scripted(probs, min_silence_ms=300, speech_pad_ms=0)
    segs = feed_all(v, len(probs))
    ev = v.drain_events()
    assert [e["type"] for e in ev] == ["speech_start", "speech_end"]
    assert ev[0]["seg"] == ev[1]["seg"] == segs[0].seg == 0
    assert ev[0]["t"] == pytest.approx(5 * F, abs=1e-3)
    assert ev[0]["t_detect"] == pytest.approx(6 * F, abs=1e-3)  # start_frames=1：最初の枠の終わりで確定
    assert (ev[1]["t0"], ev[1]["t1"]) == (round(segs[0].t0, 3), round(segs[0].t1, 3))
    assert ev[1]["t_detect"] > ev[1]["t1"]  # 黙ってから min_silence 経って閉じる
    assert segs[0].to_dict()["seg"] == 0


def test_vad_start_frames_ignores_short_blip():
    probs = [0.0] * 5 + [0.9] * 2 + [0.0] * 10 + [0.9] * 20 + [0.0] * 15
    v = scripted(probs, start_frames=3, speech_pad_ms=0)
    segs = feed_all(v, len(probs))
    ev = v.drain_events()
    assert [e["type"] for e in ev] == ["speech_start", "speech_end"]
    assert ev[0]["t"] == pytest.approx(17 * F, abs=1e-3)
    assert ev[0]["t_detect"] == pytest.approx(20 * F, abs=1e-3)  # 3 枠続いた時点
    assert len(segs) == 1
    cands = [r for s, r in v.drain_trace() if s == "vad_cand"]
    assert cands == [{"t0": round(5 * F, 3), "t1": round(7 * F, 3), "frames": 2}]
    # start_frames=1 なら同じ確率列で 2 つ始まる（短い方は min_speech で捨てられる）
    v1 = scripted(probs, start_frames=1, speech_pad_ms=0)
    feed_all(v1, len(probs))
    assert [e["type"] for e in v1.drain_events()] == ["speech_start", "speech_end", "speech_dropped", "speech_start", "speech_end"]


def test_vad_short_segment_is_dropped_with_end():
    probs = [0.0] * 3 + [0.9] * 3 + [0.0] * 15
    v = scripted(probs, speech_pad_ms=0, min_speech_ms=250)
    assert feed_all(v, len(probs)) == []
    ev = v.drain_events()
    assert [e["type"] for e in ev] == ["speech_start", "speech_end", "speech_dropped"]
    assert ev[1]["dropped"] == "vad_short" and ev[2] == {"type": "speech_dropped", "seg": 0, "reason": "vad_short"}


class FakeStt:
    name = "fake"

    def __init__(self, empty_segs=()):
        self.empty = set(empty_segs)
        self.calls = 0

    def transcribe(self, audio, t0, sr):
        i, self.calls = self.calls, self.calls + 1
        if i in self.empty:
            return []
        return [SttResult(t0=t0, t1=t0 + len(audio) / sr, text=f"u{i}", lang="ja")]


class Recorder:
    def __init__(self):
        self.msgs = []

    def start(self, info): self.msgs.append({"type": "hello"})
    def audio(self, chunk): pass
    def trace(self, stage, obj): pass
    def event(self, msg): self.msgs.append(msg)
    def emit(self, utt, seg=None, timing=None): self.msgs.append({"type": "utterance", "id": utt.id, "seg": seg, "timing": timing})
    def revise(self, utts): pass
    def end(self, stats): self.msgs.append({"type": "end"})
    def close(self): pass


def run_scripted(tmp_path, probs, stt, stt_thread=False):
    cfg = load_config(None, ["vad.provider=none", f"pipeline.stt_thread={str(stt_thread).lower()}"])
    rec = Recorder()
    p = Pipeline(cfg, "20261110_000000", MultiSink([rec, JsonlSink(tmp_path / "run")]), run_name="t")
    p.vad = scripted(probs, speech_pad_ms=0)
    p.stt = stt
    x = np.zeros(len(probs) * FRAME, dtype=np.float32)
    chunks = [AudioChunk(t0=i / 16000, samples=x[i : i + 1024], sample_rate=16000) for i in range(0, len(x), 1024)]
    p.run(chunks, "replay:fast")
    return rec.msgs


@pytest.mark.parametrize("stt_thread", [False, True])
def test_pipeline_order_start_end_utterance(tmp_path, stt_thread):
    probs = ([0.0] * 5 + [0.9] * 20 + [0.0] * 15) * 3
    msgs = run_scripted(tmp_path, probs, FakeStt(empty_segs={1}), stt_thread)
    types = [m["type"] for m in msgs]
    assert types[0] == "hello" and types[-1] == "end"
    by_seg = {}
    for m in msgs:
        if "seg" in m:
            by_seg.setdefault(m["seg"], []).append(m["type"])
    assert by_seg == {0: ["speech_start", "speech_end", "utterance"],
                      1: ["speech_start", "speech_end", "speech_dropped"],
                      2: ["speech_start", "speech_end", "utterance"]}
    drop = next(m for m in msgs if m["type"] == "speech_dropped")
    assert drop["reason"] == "stt_empty"
    u = [m for m in msgs if m["type"] == "utterance"]
    assert [x["id"] for x in u] == ["utt_000000", "utt_000001"]  # id は発話レコードの通し番号のまま（seg とは別）
    for x in u:
        assert x["timing"]["stt_done_unix"] >= x["timing"]["vad_close_unix"]
    assert types.index("speech_start") < types.index("speech_end") < types.index("utterance")
    ev = [json.loads(x) for x in (tmp_path / "run" / "trace" / "events.jsonl").read_text().splitlines()]
    assert [e["type"] for e in ev if e["type"] != "utterance"] == [m["type"] for m in msgs if m["type"].startswith("speech_")]
    assert all("t_unix" in e for e in ev)
    vad = [json.loads(x) for x in (tmp_path / "run" / "trace" / "vad.jsonl").read_text().splitlines()]
    assert [r["seg"] for r in vad] == [0, 1, 2]


def events_of(store, sid, cfg, chunk_ms, speed="fast"):
    rec = Recorder()
    p = Pipeline(cfg, sid, MultiSink([rec]), run_name="t")
    p.run(WavReplay(store, sid, speed=speed, chunk_ms=chunk_ms).chunks(), f"replay:{speed}")
    return [{k: v for k, v in m.items() if k != "t_unix"} for m in rec.msgs if m["type"].startswith("speech_")]


@pytest.mark.skipif(not SAMPLE.exists(), reason="samples/conv2_ja_kokoro.wav が無い")
def test_replay_reproduces_events(store, tmp_path):
    clip = tmp_path / "clip.wav"
    x, sr = sf.read(SAMPLE, dtype="float32")
    sf.write(clip, x[: 6 * sr], sr)
    sid = import_audio(store, clip, label="live")
    cfg = load_config("config/silero.yaml", ["vad.start_frames=2"])
    a = events_of(store, sid, cfg, 32)
    assert [m["type"] for m in a][:2] == ["speech_start", "speech_end"] and len(a) >= 2
    assert events_of(store, sid, cfg, 100) == a  # チャンクの長さが違っても同じ通知が同じ時刻で
    assert events_of(store, sid, cfg, 20, speed="realtime") == a  # 実時間で流しても同じ


def test_websocket_events_and_utterance_compat():
    import aiohttp

    from intake.sink.websocket import WebSocketSink

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    ws = WebSocketSink(port=port)
    u0 = Utterance(id="utt_000000", session="20261110_000000", t0=0.0, t1=1.0, text="a")
    try:
        ws.start({"session": "20261110_000000", "run": "t"})
        ws.event({"type": "speech_start", "seg": 0, "t": 0.0, "t_detect": 0.032, "t_unix": 1.0, "p": 0.9})  # 購読前：再送されない
        ws.emit(u0, seg=0, timing={"vad_close_unix": 1.0, "stt_done_unix": 1.2})

        async def client():
            async with aiohttp.ClientSession() as cs:
                async with cs.ws_connect(f"http://127.0.0.1:{port}/ws") as c:
                    msgs = [json.loads((await c.receive(timeout=5)).data) for _ in range(2)]
                    await asyncio.to_thread(ws.event, {"type": "speech_start", "seg": 1, "t": 2.0, "t_detect": 2.032, "t_unix": 3.0, "p": 0.8})
                    msgs.append(json.loads((await c.receive(timeout=5)).data))
                    await asyncio.to_thread(ws.emit, u0)  # seg / timing なしでも従来どおり
                    msgs.append(json.loads((await c.receive(timeout=5)).data))
                    return msgs

        msgs = asyncio.run(client())
        assert [m["type"] for m in msgs] == ["hello", "utterance", "speech_start", "utterance"]
        assert msgs[1]["record"] == u0.to_dict() and "seg" not in msgs[1]["record"]  # 発話レコードの形は変えない
        assert msgs[1]["seg"] == 0 and msgs[1]["timing"]["stt_done_unix"] == 1.2
        assert msgs[2]["seg"] == 1
        assert set(msgs[3]) == {"type", "record"}
    finally:
        ws.shutdown()
