import json

import numpy as np
import pytest
import soundfile as sf

from intake.phones.align import align_session, align_utterance, edit_align
from intake.phones.allo import parse_timestamped
from intake.phones.base import assign_phones
from intake.phones.classes import load_table, looks_arpabet, split_ipa
from intake.phones.wav2vec2_espeak import ctc_greedy
from intake.schema import Phone, SchemaError, Utterance, Word, validate_utterance

SID = "20261109_000000"


def test_schema_02_reads_01_records():
    d01 = {"id": "utt_000001", "session": SID, "t0": 0.0, "t1": 1.0, "text": "hi", "speaker_source": "none", "words": []}
    u = Utterance.from_dict(d01)
    assert u.phones == [] and u.phones_model is None and u.phones_source == "none"


def test_schema_02_roundtrip_and_rejects():
    u = Utterance(id="utt_000002", session=SID, t0=1.0, t1=2.0, text="rice", words=[Word("rice", 1.0, 1.5, 0.9)],
                  phones=[Phone("ɹ", 1.02, 1.04, 0.8), Phone("aɪ", 1.1, 1.12, 0.95)], phones_model="m", phones_source="model")
    d = u.to_dict()
    assert Utterance.from_dict(d).to_dict() == d
    ref = Utterance(id="utt_000003", session=SID, t0=0, t1=2, phones=[Phone("R", w=0)], phones_source="g2p")
    assert Utterance.from_dict(ref.to_dict()).phones[0].w == 0
    with pytest.raises(SchemaError):  # 認識器の音素は時刻を持つ
        validate_utterance({**d, "phones": [{"ph": "a"}]})
    with pytest.raises(SchemaError):
        validate_utterance({**d, "phones_source": "guess"})


def test_classes_table_and_roundtrip():
    t = load_table()
    assert 38 <= len(t.classes) <= 45
    assert split_ipa("pʰliːz kɔːl") == ["pʰ", "l", "iː", "z", " ", "k", "ɔː", "l"]
    assert split_ipa("t͡ʃeɪn") == ["t͡ʃ", "e", "ɪ", "n"]
    assert [c.cls for c in t.classify_ipa(split_ipa("t͡ʃeɪn"))] == ["CH", "EY", "N"]
    # 二重母音・破擦音は 1 クラスに。語をまたいではまとめない
    assert [c.cls for c in t.classify_ipa(["ð", "ə", " ", "ɹ", "a", "ɪ", "s"])] == ["DH", "AH", "R", "AY", "S"]
    assert [c.cls for c in t.classify_ipa(["t", " ", "ʃ"])] == ["T", "SH"]
    # 認識器のトークン（eSpeak 表記）と G2P（ARPAbet）が同じクラスに来る
    ipa = [c.cls for c in t.classify_ipa(["ɹ", "aɪ", "s"])]
    arpa = [c.cls for c in t.classify_arpabet(["R", "AY1", "S"])]
    assert ipa == arpa == ["R", "AY", "S"]
    assert looks_arpabet(["R", "AY1", "S"]) and not looks_arpabet(["ɹ", "aɪ"])
    # 表に無い記号は隠さず ? を付けて残す。元の位置に戻せる
    cs = t.classify_ipa(["k", "ʘ"])
    assert cs[1].cls == "?ʘ" and cs[1].src == (1,)


def test_edit_align_small():
    assert [o for o, _, _ in edit_align(list("RAYS"), list("LAYS"))] == ["sub", "match", "match", "match"]
    assert [o for o, _, _ in edit_align(list("ABCD"), list("ACDE"))] == ["match", "del", "match", "match", "ins"]
    assert edit_align([], ["X"]) == [("ins", None, 0)]


def _hyp(phs, t=1.0):
    out = []
    for p in phs:
        out.append(Phone(p, t, t + 0.02, 0.9))
        t += 0.1
    return out


def test_align_utterance_rice_lice():
    ref = Utterance(id="utt_000000", session=SID, t0=0.5, t1=3.0, text="some rice", phones_source="g2p",
                    phones=[Phone("S", w=0), Phone("AH1", w=0), Phone("M", w=0), Phone("R", w=1), Phone("AY1", w=1), Phone("S", w=1)])
    hyp = Utterance(id="utt_000000", session=SID, t0=0.9, t1=2.0, text="some lice", phones_source="model", phones_model="m",
                    words=[Word("some", 1.0, 1.3, 0.9), Word("lice", 1.3, 1.6, 0.4)], phones=_hyp(["s", "ʌ", "m", "l", "a", "ɪ", "s"]))
    row = align_utterance(ref, [hyp], load_table())
    assert row["counts"] == {"match": 5, "sub": 1, "del": 0, "ins": 0}
    sub = [o for o in row["ops"] if o["op"] == "sub"][0]
    assert (sub["ref"], sub["hyp"], sub["hyp_ph"], sub["w"]) == ("R", "L", "l", 1)
    ay = [o for o in row["ops"] if o["ref"] == "AY"][0]
    assert ay["hyp_ph"] == "aɪ" and ay["t0"] == pytest.approx(1.4) and ay["t1"] == pytest.approx(1.52)
    assert row["words"]["counts"]["sub"] == 1 and row["words"]["ops"][1]["hyp"] == "lice"


def test_align_utterance_deletion_has_position():
    ref = Utterance(id="utt_000000", session=SID, t0=0, t1=3, phones_source="g2p",
                    phones=[Phone(p, w=0) for p in ["S", "T", "R", "IY1", "T"]])
    hyp = Utterance(id="utt_000000", session=SID, t0=0.9, t1=2, phones_source="model", phones=_hyp(["s", "t", "ɹ", "iː"]))
    row = align_utterance(ref, [hyp], load_table())
    d = [o for o in row["ops"] if o["op"] == "del"]
    assert len(d) == 1 and d[0]["pos"] == "gap" and d[0]["t0"] >= 1.3


def test_assign_phones_to_utterances():
    us = [Utterance(id="utt_000000", session=SID, t0=0, t1=1), Utterance(id="utt_000001", session=SID, t0=1.2, t1=2)]
    rest = assign_phones(us, [Phone("a", 0.1, 0.12), Phone("b", 1.1, 1.12), Phone("c", 1.5, 1.52)], "m")
    assert rest == [] and [p.ph for p in us[0].phones] == ["a"] and [p.ph for p in us[1].phones] == ["b", "c"]
    assert us[0].phones_source == "model" and us[0].phones_model == "m"
    assert assign_phones([], [Phone("a", 0, 0.1)], "m")[0].ph == "a"


def test_ctc_greedy_and_allosaurus_parse():
    vocab = {0: "<pad>", 1: "h", 2: "ə", 3: "<s>"}
    probs = np.array([[0.9, .05, .05, 0], [0.1, .8, .1, 0], [0.1, .7, .2, 0], [0.9, .05, .05, 0], [0, .1, .9, 0], [0, 0, 0, 1]])
    ph = ctc_greedy(probs, vocab, 0, 10.0, 0.02)
    assert [p.ph for p in ph] == ["h", "ə"]
    assert ph[0].t0 == pytest.approx(10.02) and ph[0].t1 == pytest.approx(10.06) and ph[0].p == pytest.approx(0.75)
    got = parse_timestamped("0.210 0.045 h (0.912) ə (0.031)\n0.390 0.045 ɛ\n", 5.0)
    assert [(p.ph, round(p.t0, 3), p.p) for p in got] == [("h", 5.21, 0.912), ("ɛ", 5.39, None)]


def test_ref_to_run_and_align_session(tmp_path):
    import sys

    sys.path.insert(0, "tools")
    from ref_to_run import main as ref_main

    sd = tmp_path / "s"
    sdir = sd / SID
    (sdir / "runs" / "phones").mkdir(parents=True)
    sf.write(sdir / "audio.wav", np.zeros(48000, dtype=np.float32), 16000)
    hyp = Utterance(id="utt_000000", session=SID, t0=0.9, t1=2.0, text="rice", phones_source="model", phones_model="m",
                    phones=_hyp(["l", "a", "ɪ", "s"]))
    (sdir / "runs" / "phones" / "utterances.jsonl").write_text(json.dumps(hyp.to_dict()) + "\n", encoding="utf-8")
    (sdir / "runs" / "phones" / "run.json").write_text("{}", encoding="utf-8")
    refj = tmp_path / "r.json"
    refj.write_text(json.dumps({"group": "japanese", "lines": [{"text": "rice", "phones": "ɾaɪs"}]}), encoding="utf-8")
    ref_main([SID, str(refj), "--g2p", "none", "--sessions-dir", str(sd)])
    info = json.loads((sdir / "runs" / "ref" / "run.json").read_text(encoding="utf-8"))
    assert info["ref"]["source"] == "human" and info["group"] == "japanese"
    u = Utterance.from_dict(json.loads((sdir / "runs" / "ref" / "utterances.jsonl").read_text(encoding="utf-8")))
    assert u.t1 == pytest.approx(3.0) and [p.ph for p in u.phones] == ["ɾ", "a", "ɪ", "s"] and u.phones_source == "human"
    rows = [json.loads(x) for x in (sdir / "runs" / "phones" / "trace" / "phones_align.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0]["ref_run"] == "ref" and rows[0]["group"] == "japanese"
    assert [(o["op"], o["ref"], o["hyp"]) for o in rows[0]["ops"]] == [("sub", "DX", "L"), ("match", "AY", "AY"), ("match", "S", "S")]
    assert align_session(sdir, refs=["nothing"]) == []
