"""発話ごとの text と（あれば）人間の音素転写から、閲覧画面で比べるための参照 run を書く。

  python tools/ref_to_run.py <session> ref.json [--g2p g2p_en|espeak|none] [--group japanese]

ref.json（samples/*.script.json もそのまま使える）:
  {"name": "...", "group": "japanese", "source": "saa:japanese1", "note": "...",
   "lines": [{"t0": 0.0, "t1": 25.3, "text": "Please call Stella.", "phones": "pʰliːz kʰɔl stɛlə", "speaker": "A"}]}
  - t0/t1 が無い行は録音全体（0〜長さ）とみなす。--locate-run R を付けると、run R の STT の単語列から
    text の読まれた範囲を探して t0/t1 にする（台本の朗読の後に自由発話が続く録音など）
  - phones は人間の音声転写（IPA。空白で語を区切る）。あれば run「ref」（phones_source: human）に入れる
  - text は G2P で辞書上の音素にして run「ref_g2p」（phones_source: g2p）に入れる。--g2p none で作らない

どちらも観察ではなく参照。通常の run には現れない。書いた後、同じセッションの音素つき run を整列し直す
（trace/phones_align.jsonl）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from intake.phones.align import align_session, edit_align, norm_words  # noqa: E402
from intake.phones.classes import split_ipa  # noqa: E402
from intake.schema import Phone, Utterance, make_utterance_id  # noqa: E402
from intake.session import SessionStore  # noqa: E402


def human_phones(s: str) -> list[Phone]:
    out, w = [], 0
    for tok in split_ipa(s):
        if tok == " ":
            w += 1
            continue
        out.append(Phone(ph=tok, w=w))
    return out


def speaker_of(ln: dict) -> str | None:
    s = ln.get("speaker")
    if not s:
        return None
    return f"spk_{ord(s) - 64}" if len(s) == 1 and s.isalpha() else str(s)


def match_cluster(text: str, words: list, max_gap: int = 20) -> list[tuple[int, int]]:
    """text の単語列と STT の単語列を整列し、一致の対 (参照の語番号, STT の語番号) のうち、いちばん密な塊を返す。

    整列は全体にわたるので、朗読の後の自由発話にも「a」「the」などがまばらに一致することがある。STT の語番号が
    max_gap 語を超えて離れたところで塊を切り、一致の数が最も多い塊だけを使う。"""
    hw = [(norm_words(w.w) or [""])[0] for w in words]
    ops = [(i, j) for op, i, j in edit_align(norm_words(text), hw) if op == "match"]
    if not ops:
        return []
    clusters, cur = [], [ops[0]]
    for p in ops[1:]:
        if p[1] - cur[-1][1] > max_gap:
            clusters.append(cur)
            cur = []
        cur.append(p)
    clusters.append(cur)
    return max(clusters, key=len)


def locate(text: str, words: list, pad: float = 0.3, max_gap: int = 20) -> tuple[float, float] | None:
    """text の読まれた範囲の時刻（一致した最初と最後の単語、前後に pad 秒）。"""
    ops = match_cluster(text, words, max_gap)
    if len(ops) < 3:
        return None
    a, b = words[ops[0][1]], words[ops[-1][1]]
    return max(0.0, a.t0 - pad), b.t1 + pad


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def trim_to_read(text: str, words: list, max_gap: int = 20) -> str:
    """読まれた文だけを残す。台本の一部だけを読む録音向け。

    文ごとに一致した語の割合を数え、半分以上一致した最初の文から最後の文までを残す（読み終えた後の自由発話に
    台本の後半の語がまばらに一致しても、その文は残さない）。"""
    ops = match_cluster(text, words, max_gap)
    if len(ops) < 3:
        return text
    sents = sentences(text)
    sent_of, sizes = [], []
    for k, s in enumerate(sents):
        m = len(norm_words(s))
        sizes.append(m)
        sent_of += [k] * m
    hit = [0] * len(sents)
    for i, _ in ops:
        hit[sent_of[i]] += 1
    read = [k for k in range(len(sents)) if sizes[k] and hit[k] * 2 >= sizes[k]]
    if not read:
        return text
    return " ".join(sents[read[0] : read[-1] + 1])


def write_run(store: SessionStore, sid: str, run: str, utts: list[Utterance], ref: dict, group: str | None, source: str,
              note: str, lines: list[dict] = ()) -> Path:
    rd = store.run_dir(sid, run)
    (rd / "trace").mkdir(parents=True, exist_ok=True)
    with (rd / "utterances.jsonl").open("w", encoding="utf-8") as f:
        for u in utts:
            f.write(json.dumps(u.to_dict(), ensure_ascii=False) + "\n")
    (rd / "run.json").write_text(json.dumps({
        "session": sid, "run": run, "source": source,
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "providers": {"vad": "ref", "stt": "ref", "speaker": "ref", "phones": ref["by"]},
        "ref": ref, "group": group, "config": {"note": note},
        "lines": [{k: ln[k] for k in ("t0", "t1", "located_by", "trimmed") if k in ln} for ln in lines],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return rd


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("session")
    ap.add_argument("ref")
    ap.add_argument("--g2p", default="g2p_en", choices=["g2p_en", "espeak", "none"])
    ap.add_argument("--group", help="話者の属性（例 japanese / native）。閲覧画面で群ごとに切り替える")
    ap.add_argument("--lang", default=None)
    ap.add_argument("--sessions-dir", default="sessions")
    ap.add_argument("--locate-run", help="t0/t1 の無い行を、この run の単語列から探して決める")
    ap.add_argument("--trim-to-read", action="store_true",
                    help="--locate-run と併用。台本の一部だけが読まれたとき、読まれた文だけを参照の text に残す")
    a = ap.parse_args(argv)

    store = SessionStore(a.sessions_dir)
    sid = a.session
    ref = json.loads(Path(a.ref).read_text(encoding="utf-8"))
    group = a.group or ref.get("group")
    lang = a.lang or ref.get("lang") or "en"
    source = ref.get("source") or f"ref:{ref.get('name', Path(a.ref).name)}"
    wav = store.audio_path(sid)
    dur = sf.info(str(wav)).duration if wav.exists() else None
    lines = ref["lines"]
    if a.locate_run:
        rd = store.run_dir(sid, a.locate_run)
        hw = []
        if (rd / "utterances.jsonl").exists():
            for x in (rd / "utterances.jsonl").read_text(encoding="utf-8").splitlines():
                if x.strip():
                    hw.extend(Utterance.from_dict(json.loads(x)).words)
        hw.sort(key=lambda w: w.t0)
        for ln in lines:
            if ln.get("t0") is not None or not ln.get("text"):
                continue
            if a.trim_to_read and not ln.get("phones"):  # 人間の転写がある行は転写の範囲に合わせて切らない
                t = trim_to_read(ln["text"], hw)
                if t != ln["text"]:
                    ln["text"], ln["trimmed"] = t, True
            if span := locate(ln["text"], hw):
                ln["t0"], ln["t1"] = round(span[0], 3), round(span[1], 3)
                ln["located_by"] = a.locate_run

    def base(i, ln, phones, src, model):
        t0 = float(ln.get("t0", 0.0))
        t1 = float(ln["t1"]) if ln.get("t1") is not None else (dur or t0)
        return Utterance(id=make_utterance_id(i), session=sid, t0=t0, t1=t1, text=ln.get("text", ""), lang=lang,
                         speaker=speaker_of(ln), speaker_conf=1.0 if ln.get("speaker") else None,
                         speaker_source="script" if ln.get("speaker") else "none",
                         phones=phones, phones_model=model, phones_source=src)

    written = []
    if any(ln.get("phones") for ln in lines):
        by = ref.get("transcriber") or "human"
        utts = [base(i, ln, human_phones(ln.get("phones") or ""), "human", by) for i, ln in enumerate(lines)]
        note = ref.get("note") or "人間の音声転写（参照）。観察ではない"
        written.append(write_run(store, sid, "ref", utts, {"source": "human", "by": by}, group, source, note, lines))
    if a.g2p != "none":
        try:
            from intake.phones.g2p import G2P  # noqa: PLC0415

            g = G2P(a.g2p)
        except Exception as e:  # noqa: BLE001
            print(f"[ref_to_run] G2P（{a.g2p}）が使えないので ref_g2p は作らない: {e}", file=sys.stderr)
        else:
            utts = [base(i, ln, g.phones(ln.get("text", "")), "g2p", g.name) for i, ln in enumerate(lines)]
            note = (f"G2P（{g.name}）で text を辞書上の音素にしたもの。『辞書上はこう読むはず』であって観察ではない。"
                    "人が実際にどう発音したかは表していない")
            written.append(write_run(store, sid, "ref_g2p", utts, {"source": "g2p", "by": g.name}, group, source, note, lines))
    for rd in written:
        print(rd)
    for p in align_session(store.dir(sid)):
        print(f"整列: {p}")


if __name__ == "__main__":
    main()
