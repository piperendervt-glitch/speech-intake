"""参照の音素列（human / g2p）と認識の音素列（model）を、粗いクラスの上で編集距離により整列する。

各位置を 一致(match) / 置換(sub) / 脱落(del：参照にあって認識に無い) / 挿入(ins：認識にだけある) に分ける。
これは「並べた結果」であって誤りの判定ではない（判定は利用側が推察表に書く）。

結果は認識側の run の trace/phones_align.jsonl（閲覧画面用の記録。発話レコードには入れない）。
1 行 = 参照 run の発話 1 つ。その時間に入る認識側の音素（複数の発話にまたがってよい）をまとめて整列する。

  python -m intake align <session> [--run phones] [--ref ref ref_g2p]
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Sequence

from ..schema import Utterance
from .classes import ClassTable, CPhone, load_table, looks_arpabet

OPS = ("match", "sub", "del", "ins")
_WORD = re.compile(r"[^\w']+", re.UNICODE)


def edit_align(ref: Sequence[str], hyp: Sequence[str]) -> list[tuple[str, int | None, int | None]]:
    """レーベンシュタイン距離（置換・脱落・挿入とも 1）で整列。返り値は (op, 参照の位置, 認識の位置) の列。"""
    n, m = len(ref), len(hyp)
    D = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        D[i][0] = i
    for j in range(1, m + 1):
        D[0][j] = j
    for i in range(1, n + 1):
        ri = ref[i - 1]
        row, prev = D[i], D[i - 1]
        for j in range(1, m + 1):
            row[j] = min(prev[j - 1] + (ri != hyp[j - 1]), prev[j] + 1, row[j - 1] + 1)
    out: list[tuple[str, int | None, int | None]] = []
    i, j = n, m
    while i > 0 or j > 0:  # 対角（一致・置換）を優先して戻る
        if i > 0 and j > 0 and D[i][j] == D[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]):
            out.append(("match" if ref[i - 1] == hyp[j - 1] else "sub", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i > 0 and D[i][j] == D[i - 1][j] + 1:
            out.append(("del", i - 1, None))
            i -= 1
        else:
            out.append(("ins", None, j - 1))
            j -= 1
    out.reverse()
    return out


def norm_words(text: str) -> list[str]:
    return [w for w in (_WORD.sub(" ", text.lower()).split()) if w.strip("'")]


def _classify_ref(u: Utterance, table: ClassTable) -> tuple[list[CPhone], list[int | None]]:
    """参照の音素を語ごとにクラスへ（語をまたいで二重母音などをまとめない）。返り値はクラス列と各位置の語番号。"""
    phs = [p.ph for p in u.phones]
    arpa = looks_arpabet(phs)
    seq: list[str] = []
    for k, p in enumerate(u.phones):  # 語の切れ目に " " を挟む
        if k and p.w != u.phones[k - 1].w:
            seq.append(" ")
        seq.append(p.ph)
    idx = [k for k, x in enumerate(seq) if x != " "]  # seq の位置 → u.phones の位置
    cps = table.classify(seq, "arpabet" if arpa else "ipa")
    out, words = [], []
    for c in cps:
        src = tuple(idx.index(s) for s in c.src if s in idx)
        out.append(CPhone(c.cls, c.orig, src))
        words.append(u.phones[src[0]].w if src else None)
    return out, words


def align_utterance(ref: Utterance, hyps: list[Utterance], table: ClassTable) -> dict[str, Any]:
    """参照の発話 1 つと、その時間に入る認識側の発話（の音素・単語）を整列する。"""
    hp = [p for u in hyps for p in u.phones if p.t0 is not None and ref.t0 <= (p.t0 + p.t1) / 2 <= ref.t1]
    hp.sort(key=lambda p: p.t0)
    rc, rw = _classify_ref(ref, table)
    hc = table.classify_ipa([p.ph for p in hp])
    ops = edit_align([c.cls for c in rc], [c.cls for c in hc])

    def hyp_span(c: CPhone) -> tuple[float, float, float | None]:
        ps = [hp[s] for s in c.src]
        probs = [p.p for p in ps if p.p is not None]
        return ps[0].t0, ps[-1].t1, (min(probs) if probs else None)

    rows: list[dict[str, Any]] = []
    counts = dict.fromkeys(OPS, 0)
    last_t = hp[0].t0 if hp else ref.t0
    for k, (op, i, j) in enumerate(ops):
        counts[op] += 1
        r = {"op": op, "ref": rc[i].cls if i is not None else None, "hyp": hc[j].cls if j is not None else None,
             "ref_ph": rc[i].orig if i is not None else None, "hyp_ph": hc[j].orig if j is not None else None}
        if i is not None and rw[i] is not None:
            r["w"] = rw[i]
        if j is not None:
            t0, t1, p = hyp_span(hc[j])
            r.update(t0=round(t0, 3), t1=round(t1, 3))
            if p is not None:
                r["p"] = round(p, 3)
            last_t = t1
        else:  # 脱落：認識側の前後の音素の間に置く（時刻は推定位置）
            nxt = next((hyp_span(hc[jj])[0] for _, _, jj in ops[k + 1 :] if jj is not None), max(last_t, ref.t1 if not hp else last_t))
            r.update(t0=round(last_t, 3), t1=round(max(last_t, nxt), 3), pos="gap")
        rows.append(r)

    # 単語：参照の text と、STT の単語列（同じ時間に入るもの）
    rws = norm_words(ref.text)
    hw = [w for u in hyps for w in u.words if ref.t0 <= (w.t0 + w.t1) / 2 <= ref.t1]
    hw.sort(key=lambda w: w.t0)
    hwn = [(norm_words(w.w) or [""])[0] for w in hw]
    wops = []
    for op, i, j in edit_align(rws, hwn):
        r = {"op": op, "ref": rws[i] if i is not None else None, "hyp": hw[j].w if j is not None else None}
        if j is not None:
            r.update(t0=round(hw[j].t0, 3), t1=round(hw[j].t1, 3))
            if hw[j].p is not None:
                r["p"] = round(hw[j].p, 3)
        wops.append(r)
    wcounts = {o: sum(1 for x in wops if x["op"] == o) for o in OPS}
    return {
        "ref_utt": ref.id, "t0": round(ref.t0, 3), "t1": round(ref.t1, 3), "ref_source": ref.phones_source,
        "ref_text": ref.text, "hyp_utts": sorted({u.id for u in hyps if any(p in u.phones for p in hp) or any(w in u.words for w in hw)}),
        "phones_model": next((u.phones_model for u in hyps if u.phones_model), None),
        "counts": counts, "ops": rows, "words": {"counts": wcounts, "ops": wops},
    }


def _read_utts(p: Path) -> list[Utterance]:
    if not p.exists():
        return []
    return [Utterance.from_dict(json.loads(x)) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _info(rd: Path) -> dict[str, Any]:
    f = rd / "run.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def is_ref_run(rd: Path) -> bool:
    return bool(_info(rd).get("ref"))


def align_runs(run_dir: Path, ref_dirs: list[Path], table_path: str | None = None) -> Path:
    """認識側の run 1 つを、参照 run（複数可）の発話ごとに整列して trace/phones_align.jsonl に書く。"""
    table = load_table(table_path)
    hyps = [u for u in _read_utts(run_dir / "utterances.jsonl")]
    out = run_dir / "trace" / "phones_align.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for rd in ref_dirs:
            info = _info(rd)
            for ref in _read_utts(rd / "utterances.jsonl"):
                if not ref.phones:
                    continue
                inside = [u for u in hyps if u.t1 >= ref.t0 and u.t0 <= ref.t1]
                row = {"ref_run": rd.name, "group": info.get("group"), **align_utterance(ref, inside, table),
                       "table": str(table_path) if table_path else "classes.yaml"}
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(out)
    return out


def align_session(session_dir: Path, runs: list[str] | None = None, refs: list[str] | None = None,
                  table_path: str | None = None) -> list[Path]:
    """セッション内の「音素を持つ run」すべてを、参照 run すべてと整列する（どちらも名前で絞れる）。"""
    rdir = session_dir / "runs"
    if not rdir.exists():
        return []
    all_runs = sorted(p for p in rdir.iterdir() if p.is_dir())
    ref_dirs = [p for p in all_runs if is_ref_run(p) and (not refs or p.name in refs)]
    if not ref_dirs:
        return []
    out = []
    for rd in all_runs:
        if is_ref_run(rd) or (runs and rd.name not in runs):
            continue
        us = _read_utts(rd / "utterances.jsonl")
        if any(u.phones_source == "model" and u.phones for u in us):
            out.append(align_runs(rd, ref_dirs, table_path))
    return out
