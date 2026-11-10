"""Common Voice 英語（CC0）から、accent 欄に Japan を含むクリップだけを拾って取り込む。

2025-10 以降、Common Voice は Mozilla Data Collective（https://datacollective.mozillafoundation.org/）でだけ配布され、
取得にはアカウント（と API キー）が要る。HuggingFace の mozilla-foundation/common_voice_* は README だけになった。
→ このツールは取得をしない。ユーザーが手元に置いた英語のデータ（展開したフォルダか .tar.gz）を読むだけ。

  python tools/import_cv_accent.py <cv-corpus-…/en または …en.tar.gz> --limit 300 [--replay -c config/phones.yaml]

参照は文（sentence）だけなので、参照 run は ref_g2p。group は japanese。話者を特定しようとしない
（client_id は使わず、クリップ名だけを残す）。
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpus_common import import_session  # noqa: E402

ACCENT = re.compile(r"japan", re.I)


def pick_rows(tsv_text: str, limit: int, accent: re.Pattern = ACCENT) -> list[dict]:
    rows = []
    for r in csv.DictReader(io.StringIO(tsv_text), delimiter="\t", quoting=csv.QUOTE_NONE):
        if accent.search(r.get("accents") or r.get("accent") or ""):
            rows.append({"path": r["path"], "sentence": r["sentence"], "accents": r.get("accents") or r.get("accent")})
            if len(rows) >= limit:
                break
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="展開した en フォルダ（validated.tsv と clips/）か、英語の .tar.gz")
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--data", default="data/cv_accent")
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("-c", "--config", default="config/phones.yaml")
    a = ap.parse_args(argv)

    root = Path(a.data)
    (root / "clips").mkdir(parents=True, exist_ok=True)
    src = Path(a.src)
    if src.is_dir():
        rows = pick_rows((src / "validated.tsv").read_text(encoding="utf-8"), a.limit)
        for r in rows:
            dst = root / "clips" / r["path"]
            if not dst.exists():
                dst.write_bytes((src / "clips" / r["path"]).read_bytes())
    else:  # tar は先頭から読むしかないので 2 回なめる（1 回目で validated.tsv、2 回目で該当クリップ）
        with tarfile.open(src, "r|*") as t:
            tsv = next((t.extractfile(m).read().decode("utf-8") for m in t if m.name.endswith("/validated.tsv")), None)
        if tsv is None:
            raise SystemExit("validated.tsv が見つからない")
        rows = pick_rows(tsv, a.limit)
        want = {r["path"] for r in rows}
        with tarfile.open(src, "r|*") as t:
            for m in t:
                name = m.name.rsplit("/", 1)[-1]
                if name in want and not (root / "clips" / name).exists():
                    (root / "clips" / name).write_bytes(t.extractfile(m).read())
    (root / "picked.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"accent に Japan を含むクリップ {len(rows)} 件")

    from intake.config import load_config  # noqa: PLC0415
    from intake.session import SessionStore  # noqa: PLC0415

    cfg = load_config(a.config)
    store = SessionStore(cfg["sessions_dir"])
    import ref_to_run  # noqa: PLC0415

    for k, r in enumerate(rows):
        label = f"cv-{Path(r['path']).stem.replace('_', '-')}"[:60]
        sid = import_session(store, root / "clips" / r["path"], label, "japanese",
                             {"corpus": "common_voice_en", "corpus_item": r["path"], "license": "CC0"})
        if a.replay:
            from intake.__main__ import main as intake_main  # noqa: PLC0415

            intake_main(["-c", a.config, "replay", sid])
        rj = root / "ref" / f"{Path(r['path']).stem}.ref.json"
        rj.parent.mkdir(exist_ok=True)
        rj.write_text(json.dumps({"group": "japanese", "lang": "en", "source": f"cv:{r['path']}",
                                  "lines": [{"text": r["sentence"]}]}, ensure_ascii=False), encoding="utf-8")
        ref_to_run.main([sid, str(rj), "--g2p", "g2p_en", "--sessions-dir", cfg["sessions_dir"]])
        print(f"{k + 1}/{len(rows)}\t{sid}")


if __name__ == "__main__":
    main()
