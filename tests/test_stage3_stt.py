from types import SimpleNamespace as NS

import numpy as np

from intake.stt.faster_whisper import FasterWhisperStt


class FakeWhisper:
    def transcribe(self, audio, **kw):
        assert kw["word_timestamps"] and not kw["vad_filter"]
        segs = [
            NS(start=0.1, end=0.9, text=" 先月の", words=[NS(word="先月", start=0.1, end=0.5, probability=0.9),
                                                         NS(word="の", start=0.5, end=0.9, probability=0.8)]),
            NS(start=1.0, end=1.6, text="未入金", words=[NS(word="未入金", start=1.0, end=9.9, probability=0.7)]),
        ]
        return iter(segs), NS(language="ja")


def test_times_are_session_based_and_clamped():
    stt = FasterWhisperStt(device="cpu", _model=FakeWhisper())
    out = stt.transcribe(np.zeros(32000, dtype=np.float32), t0=10.0, sample_rate=16000)
    assert [r.text for r in out] == ["先月の", "未入金"]
    assert out[0].t0 == 10.1 and out[0].words[1].t1 == 10.9
    assert out[1].words[0].t1 == 12.0  # 区間の終わり（10+2 秒）を越えない
    assert stt.name == "faster-whisper-large-v3"


def test_one_per_vad_joins():
    stt = FasterWhisperStt(device="cpu", one_per_vad=True, _model=FakeWhisper())
    out = stt.transcribe(np.zeros(32000, dtype=np.float32), t0=0.0, sample_rate=16000)
    assert len(out) == 1 and out[0].text == "先月の未入金" and len(out[0].words) == 3
