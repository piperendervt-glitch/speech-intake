from types import SimpleNamespace as NS

import numpy as np
import pytest

from intake.audio import AudioChunk
from intake.schema import Utterance



class FakeAnn:
    def __init__(self, turns):
        self.turns = turns

    def itertracks(self, yield_label=True):
        for a, b, l in self.turns:
            yield NS(start=a, end=b), None, l


class FakePipe:
    def __call__(self, inp, **kw):
        assert inp["sample_rate"] == 16000
        return NS(speaker_diarization=None, exclusive_speaker_diarization=FakeAnn(
            [(0.0, 2.0, "SPEAKER_07"), (2.0, 3.0, "SPEAKER_02"), (3.0, 4.0, "SPEAKER_07")]))


def test_acoustic_assigns_by_overlap():
    from intake.speaker.acoustic import PyannoteSpeaker

    sp = PyannoteSpeaker(_pipeline=FakePipe())
    sp.feed(AudioChunk(0.0, np.zeros(64000, dtype=np.float32)))
    us = [Utterance(id="utt_000000", session="20261109_000000", t0=0.5, t1=1.5),
          Utterance(id="utt_000001", session="20261109_000000", t0=1.5, t1=2.9)]
    sp.assign(us)
    assert us[0].speaker is None and us[0].speaker_source == "acoustic"
    out = sp.finalize(us)
    assert [u.speaker for u in out] == ["spk_1", "spk_2"]  # 登場順に付け替え
    assert out[1].speaker_conf == pytest.approx(0.9 / 1.4)
    tr = sp.drain_trace()
    assert [r["speaker"] for _, r in tr] == ["spk_1", "spk_2", "spk_1"]


def test_sherpa_wraps_results():
    from intake.speaker.acoustic import SherpaSpeaker

    class Res(list):
        def sort_by_start_time(self):
            return sorted(self, key=lambda r: r.start)

    class FakeD:
        sample_rate = 16000

        def process(self, x):
            assert x.dtype == np.float32
            return Res([NS(start=2.0, end=4.0, speaker=0), NS(start=0.0, end=2.0, speaker=1)])

    sp = SherpaSpeaker(_diarizer=FakeD())
    sp.feed(AudioChunk(0.0, np.zeros(64000, dtype=np.float32)))
    out = sp.finalize([Utterance(id="utt_000000", session="20261109_000000", t0=0.2, t1=1.8),
                       Utterance(id="utt_000001", session="20261109_000000", t0=2.2, t1=3.0)])
    assert [u.speaker for u in out] == ["spk_1", "spk_2"]
    assert sp.name.startswith("sherpa-onnx:")
