import asyncio
import json

import aiohttp
import numpy as np
import pytest

from intake.audio import AudioChunk
from intake.schema import Utterance
from intake.sink.websocket import WebSocketSink
from intake.speaker.wearer import WearerSpeaker


def U(i, t0, t1):
    return Utterance(id=f"utt_{i:06d}", session="20261109_000000", t0=t0, t1=t1)


def test_wearer_from_file_and_ws(tmp_path):
    f = tmp_path / "wearer.jsonl"
    f.write_text('{"t0": 1.0, "t1": 3.0}\n{"t0": 2.5, "t1": 4.0}', encoding="utf-8")  # 2 行目は書きかけ
    w = WearerSpeaker(path="wearer.jsonl", session_dir=str(tmp_path))
    us = [U(0, 1.0, 2.0), U(1, 3.0, 5.0)]
    w.assign(us)
    assert (us[0].speaker, us[0].speaker_conf) == ("wearer", 1.0)
    assert us[1].speaker == "not_wearer"
    with f.open("a", encoding="utf-8") as fh:
        fh.write("\n")
    w.on_message({"type": "wearer", "t0": 4.0, "t1": 5.0})
    out = w.finalize(us)
    assert out[1].speaker == "wearer" and out[1].speaker_conf == pytest.approx(1.0)
    assert all(u.speaker_source == "wearer" for u in out)


def test_websocket_backlog_and_live(unused_tcp_port=None):
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    got_back = []
    ws = WebSocketSink(port=port, on_message=got_back.append)
    try:
        ws.start({"session": "20261109_000000", "run": "t"})
        ws.emit(U(0, 0.0, 1.0))  # 購読前に出たもの

        async def client():
            async with aiohttp.ClientSession() as cs:
                async with cs.ws_connect(f"http://127.0.0.1:{port}/ws") as c:
                    msgs = [json.loads((await c.receive(timeout=5)).data) for _ in range(2)]
                    await asyncio.to_thread(ws.emit, U(1, 1.0, 2.0))
                    msgs.append(json.loads((await c.receive(timeout=5)).data))
                    await c.send_str(json.dumps({"type": "wearer", "t0": 0, "t1": 1}))
                    await asyncio.to_thread(ws.end, {"utterances": 2})
                    msgs.append(json.loads((await c.receive(timeout=5)).data))
                    return msgs

        msgs = asyncio.run(client())
        assert [m["type"] for m in msgs] == ["hello", "utterance", "utterance", "end"]
        assert msgs[2]["record"]["id"] == "utt_000001"
        assert got_back == [{"type": "wearer", "t0": 0, "t1": 1}]
    finally:
        ws.shutdown()


def test_websocket_refuses_non_local():
    with pytest.raises(ValueError):
        WebSocketSink(host="0.0.0.0", port=1)
