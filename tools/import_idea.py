"""IDEA（International Dialects of English Archive, dialectsarchive.com）の日本のページを取り込む。

利用条件（サイトの Terms of Use）：個人の研究目的での利用は可。音声の再配布は不可（書面の許可と料金が要る）、
テキストは短い引用まで。→ 取得したものは data/idea/ にだけ置き、リポジトリにも外部にも出さない。

各ページの録音は、台本「Comma Gets a Cure」の朗読と、自由発話（綴りの書き起こしつき）からなる。
  - 台本の朗読：台本の文を G2P にした参照 run「ref_g2p」。ページに精密な音声表記（テキスト）があれば run「ref」
    朗読の範囲は STT の単語列から探す（tools/ref_to_run.py --locate-run）ので、先に phones の run を流す
  - 自由発話：綴りの書き起こしだけを data/idea/<page>/free.txt に残す（音素の参照は作らない）

  python tools/import_idea.py --pages 1-17 --replay -c config/phones.yaml

ページの HTML の形はサイトの都合で変わりうる。抜き出しに失敗したら data/idea/<page>/page.html を見て
extract() を直す。
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpus_common import fetch, import_session  # noqa: E402

BASE = "https://www.dialectsarchive.com"
COMMA_PAGE = f"{BASE}/comma-gets-a-cure"
MP3 = re.compile(r"""https?://[^"'\s<>]+?\.mp3""", re.I)


class _Text(HTMLParser):
    """HTML を段落ごとのテキストに（script/style は捨てる）。"""

    BLOCK = {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "li", "tr", "section", "article"}

    def __init__(self):
        super().__init__()
        self.out, self.skip = [""], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.skip += 1
        if tag in self.BLOCK:
            self.out.append("")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.skip -= 1
        if tag in self.BLOCK:
            self.out.append("")

    def handle_data(self, data):
        if not self.skip:
            self.out[-1] += data


def paragraphs(page: str) -> list[str]:
    p = _Text()
    p.feed(page)
    return [" ".join(html.unescape(x).split()) for x in p.out if x.strip()]


def section(paras: list[str], start: re.Pattern, stop: re.Pattern) -> str | None:
    """見出し start の次から、見出し stop の手前までをつなげる。"""
    for i, x in enumerate(paras):
        if start.search(x):
            body = []
            rest = start.split(x, maxsplit=1)[-1].strip(" :：")
            if len(rest) > 40:
                body.append(rest)
            for y in paras[i + 1 :]:
                if stop.search(y):
                    break
                body.append(y)
            return " ".join(body).strip() or None
    return None


HEAD = re.compile(r"^(BIOGRAPHICAL|PHONETIC TRANSCRIPTION|ORTHOGRAPHIC TRANSCRIPTION|SCRIPTED SPEECH|UNSCRIPTED SPEECH|TRANSCRIPTION|"
                  r"SCHOLARLY COMMENTARY|COMMENTARY|DATE OF|Recorded by|Transcribed by)", re.I)
# 見出しは大文字で段落の頭にある。メニューの「Phonetic Transcriptions」や本文中の語に当たらないよう、大文字・行頭に限る
PHONETIC_HEAD = re.compile(r"^PHONETIC TRANSCRIPTION(?! OF UNSCRIPTED)(?: OF SCRIPTED SPEECH)?")
FREE_HEAD = re.compile(r"^(?:ORTHOGRAPHIC )?TRANSCRIPTION OF UNSCRIPTED SPEECH|^UNSCRIPTED SPEECH")
TITLE_IPA = re.compile(r"^ˈ?k\S*m\S*\s+\S*g\S*ts\S*\s+\S+\s+")  # 「ˈkʰama gɛtsɐ ˈkʰjuɹ̩ˠ」「ˈkʊmɐ gɛts ə ˈkjɚ」など


def strip_title(ipa: str) -> str:
    """精密表記の頭に題名「Comma Gets a Cure」があれば落とす（japan-12・14・15 は題名から、13・16 は本文から書かれている）。
    参照の文（text）は本文だけなので、それにそろえる。"""
    m = TITLE_IPA.match(ipa)
    if not m:
        return ipa
    rest = ipa[m.end():]
    # 「gɛtsɐ ˈkʰjuɹ̩ˠ」のように a が前の語に付いて 3 語のときと、「gɛts ə ˈkjɚ」の 4 語のときがある
    first = rest.split(maxsplit=1)
    if first and re.match(r"^ˈ?(k|kʰ)j", first[0]):
        rest = first[1] if len(first) > 1 else ""
    return rest


def extract(page: str) -> dict:
    paras = paragraphs(page)
    d = {"mp3": sorted(set(MP3.findall(page)))}
    d["phonetic"] = section(paras, PHONETIC_HEAD, HEAD)
    d["free"] = section(paras, FREE_HEAD, HEAD)
    return d


def passage(paras: list[str]) -> str | None:
    """台本ページの段落から本文だけ（「Well, here's a story」から「a fair price for a cure.」まで）。引用符は直線に。"""
    paras = [x.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"') for x in paras]
    i = next((k for k, x in enumerate(paras) if x.startswith("Well, here's a story")), None)
    if i is None:
        return None
    body = []
    for x in paras[i:]:
        body.append(x)
        if re.search(r"price for a cure\.\s*$", x):
            return " ".join(body)
    return None


RAINBOW_PAGE = f"{BASE}/the-rainbow-passage"


def rainbow_text(root: Path) -> str:
    """古い録音（japan-1〜3 など）が読む「The Rainbow Passage」（data/idea/rainbow.txt）。多くは最初の段落だけを読むので、
    参照の文は ref_to_run の --trim-to-read で読まれた文に切る。"""
    f = root / "rainbow.txt"
    if f.exists():
        return f.read_text(encoding="utf-8").strip()
    paras = paragraphs(fetch(RAINBOW_PAGE, root / "rainbow.html").decode("utf-8", "replace"))
    t = next((x for x in paras if x.startswith("When the sunlight strikes raindrops")), None)
    if t is None:
        raise SystemExit(f"Rainbow Passage の本文が見つからない。{root / 'rainbow.html'} を見て data/idea/rainbow.txt に置く")
    t = re.sub(r"\s+([,.])", r"\1", t.replace("’", "'"))
    f.write_text(t, encoding="utf-8")
    return t


def pick_passage(passages: dict[str, str], words: list) -> str:
    """STT の単語列に最もよく一致する台本の名前。"""
    import ref_to_run  # noqa: PLC0415

    return max(passages, key=lambda k: len(ref_to_run.match_cluster(passages[k], words)))


def stt_words(store, sid: str, run: str) -> list:
    from intake.schema import Utterance  # noqa: PLC0415

    f = store.run_dir(sid, run) / "utterances.jsonl"
    ws = []
    if f.exists():
        for x in f.read_text(encoding="utf-8").splitlines():
            if x.strip():
                ws.extend(Utterance.from_dict(json.loads(x)).words)
    return sorted(ws, key=lambda w: w.t0)


def comma_text(root: Path) -> str:
    """台本「Comma Gets a Cure」の本文（data/idea/comma.txt。リポジトリには入れない）。"""
    f = root / "comma.txt"
    if f.exists():
        return f.read_text(encoding="utf-8").strip()
    paras = paragraphs(fetch(COMMA_PAGE, root / "comma.html").decode("utf-8", "replace"))
    t = passage(paras)
    if t is None:
        raise SystemExit(f"台本の本文が見つからない。{root / 'comma.html'} を見て data/idea/comma.txt に本文を置く")
    f.write_text(t, encoding="utf-8")
    return t


def pages(spec: str) -> list[int]:
    out = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/idea")
    ap.add_argument("--pages", default="1-17")
    ap.add_argument("--group", default="japanese")
    ap.add_argument("--g2p", default="g2p_en")
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("-c", "--config", default="config/phones.yaml")
    ap.add_argument("--run", default=None, help="phones の run 名（省略時は設定の run_name）")
    a = ap.parse_args(argv)

    from intake.config import load_config  # noqa: PLC0415
    from intake.session import SessionStore  # noqa: PLC0415

    cfg = load_config(a.config)
    store = SessionStore(cfg["sessions_dir"])
    run = a.run or cfg.get("run_name", "phones")
    root = Path(a.data)
    passages = {"comma": comma_text(root), "rainbow": rainbow_text(root)}
    import ref_to_run  # noqa: PLC0415

    summary = []
    for n in pages(a.pages):
        name = f"japan-{n}"
        d = root / name
        page = fetch(f"{BASE}/{name}", d / "page.html").decode("utf-8", "replace")
        ex = extract(page)
        (d / "extract.json").write_text(json.dumps(ex, ensure_ascii=False, indent=1), encoding="utf-8")
        if not ex["mp3"]:
            print(f"{name}: mp3 が見つからない（{d / 'page.html'} を確認）")
            continue
        mp3 = d / "audio.mp3"
        fetch(ex["mp3"][0], mp3)
        if ex["free"]:
            (d / "free.txt").write_text(ex["free"], encoding="utf-8")
        sid = import_session(store, mp3, f"idea-{name}", a.group, {"corpus": "idea", "corpus_item": name,
                                                                 "license": "IDEA terms of use（個人の研究目的のみ。再配布不可）"})
        if a.replay:
            from intake.__main__ import main as intake_main  # noqa: PLC0415

            intake_main(["-c", a.config, "replay", sid, "--run", run])
        # 録音によって読む台本が違う（古いものは Rainbow Passage）。STT の単語列とよく合う方を使う
        which = pick_passage(passages, stt_words(store, sid, run))
        line = {"text": passages[which]}
        if ex["phonetic"]:
            line["phones"] = strip_title(ex["phonetic"])
        ref = {"name": name, "group": a.group, "lang": "en", "source": f"idea:{name}", "transcriber": "idea-transcriber",
               "passage": which,
               "note": f"IDEA の台本（{which}）の朗読部分。範囲は STT の単語列から探した（located_by）", "lines": [line]}
        rj = d / "ref.json"
        rj.write_text(json.dumps(ref, ensure_ascii=False, indent=1), encoding="utf-8")
        ref_to_run.main([sid, str(rj), "--g2p", a.g2p, "--sessions-dir", cfg["sessions_dir"], "--locate-run", run, "--trim-to-read"])
        summary.append({"page": name, "session": sid, "passage": which, "phonetic": bool(ex["phonetic"]), "free": bool(ex["free"])})
        print(f"{name}\t{sid}\t{which}\t精密表記 {'あり' if ex['phonetic'] else 'なし'}\t自由発話の書き起こし {'あり' if ex['free'] else 'なし'}")
    print(json.dumps({"pages": len(summary), "phonetic": sum(x["phonetic"] for x in summary)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
