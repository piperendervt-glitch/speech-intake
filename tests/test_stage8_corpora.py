import sys

import pytest

sys.path.insert(0, "tools")

from intake.schema import Word  # noqa: E402


def test_saa_index_counts_and_transcription():
    import import_saa as s

    idx = {f"japanese{n}.mp3": {} for n in (1, 2, 3, 10)} | {"japanese1.txt": {}, "japanese10.txt": {}, "japanese1.gif": {},
                                                               "english1.mp3": {}, "english2.mp3": {}, "english2.txt": {}, "speaker_information.xlsx": {}}
    assert s.speakers(idx, "japanese") == [1, 2, 3, 10]
    assert s.speakers(idx, "english") == [1, 2]
    assert s.read_transcription("﻿ pʰliːz  kʰɔl\n stɛlə ") == "pʰliːz kʰɔl stɛlə"
    assert s.read_transcription("No transcription yet") is None
    assert len(s.PARAGRAPH.split()) == 69


def test_idea_extract_from_html():
    import import_idea as i

    page = """<html><script>var x=1</script><h2>BIOGRAPHICAL INFORMATION</h2><p>AGE: 30</p>
    <a href="https://example.org/a/japan12.mp3">audio</a>
    <h3>PHONETIC TRANSCRIPTION</h3><p>wɛl hiɚz ə stɔɹi fɔɹ ju</p><h3>UNSCRIPTED SPEECH</h3><p>I was born in Osaka.</p>
    <h3>COMMENTARY</h3><p>x</p></html>"""
    ex = i.extract(page)
    assert ex["mp3"] == ["https://example.org/a/japan12.mp3"]
    assert ex["phonetic"] == "wɛl hiɚz ə stɔɹi fɔɹ ju"
    assert ex["free"] == "I was born in Osaka."
    assert i.pages("1-3,7") == [1, 2, 3, 7]


def test_cv_pick_rows_limit():
    import import_cv_accent as c

    tsv = "client_id\tpath\tsentence\taccents\n" + "".join(
        f"c{k}\tcommon_voice_en_{k}.mp3\tHello {k}.\t{'Japanese English' if k % 2 else 'United States English'}\n" for k in range(10))
    rows = c.pick_rows(tsv, 3)
    assert [r["path"] for r in rows] == ["common_voice_en_1.mp3", "common_voice_en_3.mp3", "common_voice_en_5.mp3"]


def test_locate_reading_span():
    import ref_to_run as r

    ws = [Word(w, t, t + 0.3) for t, w in zip(range(20), "um so please call stella ask her to bring these and then I was born".split())]
    t0, t1 = r.locate("Please call Stella. Ask her to bring these things.", ws, pad=0)
    assert (t0, t1) == pytest.approx((2.0, 9.3))
    assert r.locate("nothing matches here", ws) is None
