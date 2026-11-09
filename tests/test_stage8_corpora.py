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
    # OSF の txt：見出し行のあとに角括弧で IPA
    assert s.read_transcription("Japanese speaker 1\n\n[pliːs kol\n steɺɑ]\n") == "pliːs kol steɺɑ"
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

    # 実際のページの形：メニューに「Phonetic Transcriptions」、見出しは「… OF SCRIPTED SPEECH:」、N/A、自由発話の後に TRANSCRIBED BY
    page = """<li>Phonetic Transcriptions</li><p>PHONETIC TRANSCRIPTION OF SCRIPTED SPEECH:</p><p>ˈkʰama gɛtsɐ ˈkʰjuɹ̩ˠ wɛl</p>
    <p>TRANSCRIBED BY: X</p><p>ORTHOGRAPHIC TRANSCRIPTION OF UNSCRIPTED SPEECH:</p><p>I was born in Anjo.</p><p>TRANSCRIBED BY: X</p>
    <p>PHONETIC TRANSCRIPTION OF UNSCRIPTED SPEECH:</p><p>ˈaɪ wəz bɔ̃</p><p>TRANSCRIBED BY: X</p>"""
    ex = i.extract(page)
    assert ex["phonetic"] == "ˈkʰama gɛtsɐ ˈkʰjuɹ̩ˠ wɛl"
    assert ex["free"] == "I was born in Anjo."
    na = "<p>PHONETIC TRANSCRIPTION OF SCRIPTED SPEECH: N/A</p><p>TRANSCRIBED BY: N/A</p>"
    assert i.extract(na)["phonetic"] is None
    paras = ["Comma Gets A Cure", "Well, here’s a story for you: Sarah.", "Mid.", "a fair price for a cure.", "Comma Gets a Cure and derivative works"]
    assert i.strip_title("ˈkʰama gɛtsɐ ˈkʰjuɹ̩ˠ wɛl ˈçiəz̥ɐ") == "wɛl ˈçiəz̥ɐ"
    assert i.strip_title("ˈkʊmɐ gɛts ə ˈkjɚ wɛl̴ ˈɸ͜hi˞ɹɪzə") == "wɛl̴ ˈɸ͜hi˞ɹɪzə"
    assert i.strip_title("wɛl̴ ˈçiəz ə sɔɪ fə") == "wɛl̴ ˈçiəz ə sɔɪ fə"
    assert i.passage(paras) == "Well, here's a story for you: Sarah. Mid. a fair price for a cure."


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

    # 朗読の後の自由発話にまばらに一致する語（a / the）は、離れた塊として捨てる
    script = "The cat sat on a mat. The dog ran to the park. Birds sing at a tree."
    spoken = "the cat sat on a mat the dog ran to the park".split() + ["uh"] * 30 + "then a the".split()
    ws = [Word(w, float(t), t + 0.5) for t, w in enumerate(spoken)]
    t0, t1 = r.locate(script, ws, pad=0)
    assert (t0, t1) == pytest.approx((0.0, 11.5))
    # 台本の一部だけ読んだときは、読まれた文だけを残す
    assert r.trim_to_read(script, ws) == "The cat sat on a mat. The dog ran to the park."
