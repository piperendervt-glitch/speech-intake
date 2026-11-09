"""セッションをまたいで整列結果（trace/phones_align.jsonl）を群ごとに数える。数えるだけで、合否は決めない。

  python tools/align_report.py --run phones --ref ref [--corpus saa] [--top 25]

出力（Markdown）：群ごとの 一致・置換・脱落・挿入 の数、多い置換・脱落・挿入の対（粗いクラス）、
単語（参照の文 vs Whisper）の一致・置換・脱落・挿入。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from intake.session import SessionStore  # noqa: E402

OPS = ("match", "sub", "del", "ins")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions-dir", default="sessions")
    ap.add_argument("--run", default="phones")
    ap.add_argument("--ref", default="ref")
    ap.add_argument("--corpus", help="session.json の corpus で絞る（saa / idea / common_voice_en）")
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args(argv)
    st = SessionStore(a.sessions_dir)
    ph, wd, pairs, n = defaultdict(Counter), defaultdict(Counter), defaultdict(Counter), Counter()
    refcls = defaultdict(Counter)
    for sid in st.list_sessions():
        meta = st.read_meta(sid)
        if a.corpus and meta.get("corpus") != a.corpus:
            continue
        f = st.run_dir(sid, a.run) / "trace" / "phones_align.jsonl"
        if not f.exists():
            continue
        rows = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
        rows = [r for r in rows if r["ref_run"] == a.ref]
        if not rows:
            continue
        g = rows[0].get("group") or meta.get("group") or "?"
        n[g] += 1
        for r in rows:
            for o in r["ops"]:
                ph[g][o["op"]] += 1
                if o["ref"]:
                    refcls[g][o["ref"]] += 1
                if o["op"] != "match":
                    pairs[g][f"{o['ref'] or '∅'}→{o['hyp'] or '∅'}"] += 1
            for o in r["words"]["ops"]:
                wd[g][o["op"]] += 1
    print(f"## run={a.run} / 参照={a.ref}{' / corpus=' + a.corpus if a.corpus else ''}\n")
    print("| 群 | セッション | 参照の音素 | 一致 | 置換 | 脱落 | 挿入 | 単語：一致 | 置換 | 脱落 | 挿入 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for g in sorted(n):
        c, w = ph[g], wd[g]
        print(f"| {g} | {n[g]} | {c['match'] + c['sub'] + c['del']} | " + " | ".join(str(c[k]) for k in OPS) + " | "
              + " | ".join(str(w[k]) for k in OPS) + " |")
    for g in sorted(n):
        print(f"\n### {g}：多い対（参照→認識、∅ は無し。括弧はその参照クラスの出現数）\n")
        for k, v in pairs[g].most_common(a.top):
            r = k.split("→")[0]
            print(f"- {k}: {v}" + (f"（{refcls[g][r]}）" if r != "∅" else ""))


if __name__ == "__main__":
    main()
