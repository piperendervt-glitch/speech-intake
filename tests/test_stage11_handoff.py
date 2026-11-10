"""段11：turn-bench からの申し送り（hello の絶対パス・暫定の文字 partial・購読側からの VAD 設定・JA1 UDP 入力）。"""

import json
import socket
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from intake.audio import AudioChunk
from intake.capture.ja1 import Ja1UdpCapture, decode_ja1, encode_ja1
from intake.config import load_config
from intake.pipeline import Pipeline
from intake.sink import MultiSink

pytest.importorskip("onnxruntime")
from intake.vad.silero import FRAME  # noqa: E402

from .test_stage10_live import FakeStt, Recorder, scripted  # noqa: E402


def make_pipeline(tmp_path, probs, sets=(), stt=None):
    cfg = load_config(None, ["vad.provider=none", f"sessions_dir={tmp_path / 'sessions'}", *sets])
    rec = Recorder()
    p = Pipeline(cfg, "20261110_000000", MultiSink([rec]), run_name="t")
    p.vad = scripted(probs, speech_pad_ms=0)
    p.stt = stt or FakeStt()
    return p, rec


def chunks_of(n_frames, frames_per_chunk=1):
    x = np.zeros(n_frames * FRAME, dtype=np.float32)
    step = frames_per_chunk * FRAME
    return [AudioChunk(t0=i / 16000, samples=x[i : i + step], sample_rate=16000) for i in range(0, len(x), step)]


def run_slow(p, chunks, source="replay:fast"):
    """チャンクごとに STT のスレッドが空くのを待つ（暫定が「空いていれば頼む」なので、試験では順序を決める）。"""
    p._out("start", p.info(source))
    for c in chunks:
        p.feed(c)
        t = time.time()
        while p._jobs and time.time() - t < 5:
            time.sleep(0.001)
    p.finish()
    p._out("end", p.stats)


def test_hello_has_absolute_sessions_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = load_config(None, ["sessions_dir=rel/sessions"])
    p = Pipeline(cfg, "20261110_000000", MultiSink([]), run_name="t")
    info = p.info("mic")
    assert info["sessions_dir"] == str((tmp_path / "rel" / "sessions").resolve())
    assert Path(info["session_dir"]) == Path(info["sessions_dir"]) / "20261110_000000"
    assert info["config"]["sessions_dir"] == "rel/sessions"  # 設定に書いたものはそのまま


def test_partial_on_silence_before_speech_end(tmp_path):
    probs = [0.0] * 5 + [0.9] * 20 + [0.0] * 15
    p, rec = make_pipeline(tmp_path, probs, ["pipeline.stt_thread=true", "pipeline.partial.silence_ms=100"])
    run_slow(p, chunks_of(len(probs)))
    types = [m["type"] for m in rec.msgs if m["type"] != "hello"]
    assert types == ["speech_start", "partial", "speech_end", "utterance", "end"]
    part = next(m for m in rec.msgs if m["type"] == "partial")
    assert part["seg"] == 0 and part["reason"] == "silence" and part["text"] == "u0"
    assert part["t1"] == pytest.approx(25 * FRAME / 16000, abs=1e-3)  # 黙り始めた所まで
    assert p.stats["partials"] == 1 and p.stats["utterances"] == 1  # 暫定は発話レコードにならない


def test_partial_interval_while_speaking(tmp_path):
    probs = [0.0] * 2 + [0.9] * 60 + [0.0] * 15  # 約 1.9 秒話す
    p, rec = make_pipeline(tmp_path, probs, ["pipeline.stt_thread=true", "pipeline.partial.every_ms=500"])
    run_slow(p, chunks_of(len(probs)))
    parts = [m for m in rec.msgs if m["type"] == "partial"]
    assert len(parts) == 3 and all(m["reason"] == "interval" for m in parts)
    assert [m["t1"] > a["t1"] for a, m in zip(parts, parts[1:])] == [True, True]
    assert all(m["t1"] - m["t0"] >= 0.3 for m in parts)  # min_ms に届くまでは出さない


def test_partial_needs_stt_thread(tmp_path):
    probs = [0.0] * 5 + [0.9] * 20 + [0.0] * 15
    p, rec = make_pipeline(tmp_path, probs, ["pipeline.partial.silence_ms=100"])  # stt_thread: false
    run_slow(p, chunks_of(len(probs)))
    assert not [m for m in rec.msgs if m["type"] == "partial"]


def test_set_min_silence_from_subscriber(tmp_path):
    probs = [0.0] * 5 + [0.9] * 20 + [0.0] * 15
    p, rec = make_pipeline(tmp_path, probs)
    p.on_message({"type": "set", "vad": {"min_silence_ms": 100}})
    run_slow(p, chunks_of(len(probs)))
    cfg_ev = next(m for m in rec.msgs if m["type"] == "config")
    assert cfg_ev["vad"]["min_silence_ms"] == 100 and cfg_ev["changed"] == {"vad": {"min_silence_ms": 100}}
    end = next(m for m in rec.msgs if m["type"] == "speech_end")
    # 黙り始め（25 枠目）から 100 ms（4 枠）で閉じる。既定の 300 ms なら 10 枠
    assert end["t_detect"] == pytest.approx(29 * FRAME / 16000, abs=1e-3)
    with pytest.raises(ValueError):
        p.on_message({"type": "set", "vad": {"min_silence_ms": 0}})
    with pytest.raises(ValueError):
        p.on_message({"type": "set", "vad": {"threshold": 0.3}})
    with pytest.raises(ValueError):
        p.on_message({"type": "set", "stt": {"language": "en"}})


def test_set_passes_other_messages_to_speaker(tmp_path):
    p, _ = make_pipeline(tmp_path, [])
    got = []
    p.speaker.on_message = got.append
    p.on_message({"type": "wearer", "t0": 0, "t1": 1})
    assert got == [{"type": "wearer", "t0": 0, "t1": 1}]


def test_ja1_decode_skips_other_lines():
    pcm = (np.arange(320) - 160).astype(np.int16)
    d = encode_ja1("airpods-win", 7, 12.5, 16000, pcm) + b"JR1,airpods,1,0.0,0,0,0\nJA1,bad\n"
    pk = decode_ja1(d)
    assert len(pk) == 1 and pk[0].source == "airpods-win" and pk[0].seq == 7 and pk[0].t == 12.5
    assert np.array_equal(pk[0].pcm, pcm)


def test_ja1_udp_capture_fills_gaps(store):
    cap = Ja1UdpCapture(store, "20261110_000000", port=0)
    cap.prepare()
    sr = 16000
    tone = (0.3 * 32767 * np.sin(2 * np.pi * 220 * np.arange(320) / sr)).astype(np.int16)

    def send():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        time.sleep(0.1)
        s.sendto(encode_ja1("other", 0, 0.0, sr, tone), ("127.0.0.1", cap.port))  # 別の送り手（最初に届いた方に決まる）
        time.sleep(0.05)
        for seq in range(10):
            if seq in (4, 5, 8):  # 2 パケット（40 ms）と 1 パケット（20 ms）抜ける
                continue
            s.sendto(encode_ja1("airpods-win", seq, 100.0 + seq * 0.02, sr, tone), ("127.0.0.1", cap.port))
            time.sleep(0.005)
        s.sendto(encode_ja1("other", 1, 0.02, sr, tone), ("127.0.0.1", cap.port))
        s.close()

    cap.source = "airpods-win"
    threading.Thread(target=send, daemon=True).start()
    cap.max_samples = 10 * 320
    chunks = list(cap.chunks())
    total = sum(len(c.samples) for c in chunks)
    assert total == 10 * 320  # 抜けた 2 パケットは無音で埋まる
    assert chunks[-1].t1 == pytest.approx(0.2)
    meta = store.read_meta("20261110_000000")
    assert meta["source"] == "ja1_udp" and meta["status"] == "done"
    assert meta["ja1"]["lost_packets"] == 3 and meta["ja1"]["filled_s"] == pytest.approx(0.06) and meta["ja1"]["gaps"] == 2
    assert meta["ja1"]["ignored_other_source"] >= 1
    x, rate = sf.read(store.audio_path("20261110_000000"), dtype="int16")
    assert rate == sr and len(x) == 10 * 320
    assert not x[4 * 320 : 6 * 320].any() and not x[8 * 320 : 9 * 320].any() and x[:320].any() and x[9 * 320 :].any()


def test_ja1_only_localhost(store):
    with pytest.raises(ValueError):
        Ja1UdpCapture(store, "20261110_000000", host="0.0.0.0")
    with pytest.raises(ValueError):
        load_config(None, ["capture.udp.host=0.0.0.0"])
