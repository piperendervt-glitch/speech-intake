"""Speech Accent Archive（George Mason University, CC BY-NC-SA 4.0）を取り込む。

全員が同じ英文（下の PARAGRAPH）を読む。各サンプルに付く精密な IPA 転写（判定者による）を参照 run「ref」に、
英文の G2P を参照 run「ref_g2p」にする。配布は OSF（https://osf.io/yh23d/、ログイン不要）。

  python tools/import_saa.py --list                       # 一覧だけ取って件数を出す（data/saa/osf_index.json）
  python tools/import_saa.py --japanese all --native 44   # 日本語話者全員と、英語母語の対照群（転写のある人から番号順）
  python tools/import_saa.py ... --replay -c config/phones.yaml   # 取り込んだセッションを続けて流す

置き場所：data/saa/{mp3,txt}/（git に入らない）。セッションの group は japanese / native。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpus_common import fetch, fetch_json, import_session  # noqa: E402

OSF_NODE = "yh23d"
API = f"https://api.osf.io/v2/nodes/{OSF_NODE}/files/osfstorage/"
PARAGRAPH = ("Please call Stella. Ask her to bring these things with her from the store: Six spoons of fresh snow peas, "
             "five thick slabs of blue cheese, and maybe a snack for her brother Bob. We also need a small plastic snake "
             "and a big toy frog for the kids. She can scoop these things into three red bags, and we will go meet her "
             "Wednesday at the train station.")
NAME = re.compile(r"^(?P<lang>[a-z]+)(?P<n>\d+)\.(?P<ext>mp3|txt|gif)$")
NO_TRANSCRIPTION = re.compile(r"no transcription", re.I)


def build_index(root: Path, refresh: bool = False) -> dict[str, dict[str, str]]:
    """OSF のファイル一覧を辿り、{ファイル名: {download, size}} を data/saa/osf_index.json に保存する。"""
    idx_path = root / "osf_index.json"
    if idx_path.exists() and not refresh:
        return json.loads(idx_path.read_text(encoding="utf-8"))
    idx: dict[str, dict] = {}
    todo = [API + "?page[size]=100"]
    while todo:
        url = todo.pop()
        while url:
            d = fetch_json(url)
            for it in d.get("data", []):
                a = it["attributes"]
                if a["kind"] == "folder":
                    todo.append(it["relationships"]["files"]["links"]["related"]["href"] + "?page[size]=100")
                else:
                    idx[a["name"]] = {"download": it["links"]["download"], "size": a.get("size"), "path": a.get("materialized_path")}
            url = (d.get("links") or {}).get("next")
            print(f"\r一覧 {len(idx)} 件", end="", file=sys.stderr)
    print(file=sys.stderr)
    root.mkdir(parents=True, exist_ok=True)
    idx_path.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    return idx


def speakers(idx: dict, lang: str) -> list[int]:
    ns = {int(m["n"]) for k in idx if (m := NAME.match(k)) and m["lang"] == lang and m["ext"] == "mp3"}
    return sorted(ns)


def read_transcription(raw: str) -> str | None:
    """転写のテキスト。角括弧などは classes.yaml の drop で落ちるので、空白をそろえるだけにする。"""
    t = " ".join(raw.replace("﻿", "").split())
    if not t or NO_TRANSCRIPTION.search(t):
        return None
    return t


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/saa")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="OSF の一覧を取り直す")
    ap.add_argument("--japanese", default="all", help="all か人数")
    ap.add_argument("--native", default="same", help="英語母語の対照群の人数（same = 日本語話者と同数）。転写のある人を番号順に")
    ap.add_argument("--native-lang", default="english")
    ap.add_argument("--g2p", default="g2p_en")
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("-c", "--config", default="config/phones.yaml")
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--run")
    a = ap.parse_args(argv)

    root = Path(a.data)
    idx = build_index(root, a.refresh)
    ja = speakers(idx, "japanese")
    en = speakers(idx, a.native_lang)
    ja_txt = [n for n in ja if f"japanese{n}.txt" in idx]
    en_txt = [n for n in en if f"{a.native_lang}{n}.txt" in idx]
    print(f"OSF: japanese mp3 {len(ja)}（txt {len(ja_txt)}）、{a.native_lang} mp3 {len(en)}（txt {len(en_txt)}）")
    if a.list:
        return

    pick_ja = ja if a.japanese == "all" else ja[: int(a.japanese)]
    n_native = len(pick_ja) if a.native == "same" else int(a.native)

    from intake.config import load_config  # noqa: PLC0415
    from intake.session import SessionStore  # noqa: PLC0415

    cfg = load_config(a.config, a.set)
    store = SessionStore(cfg["sessions_dir"])
    import ref_to_run  # noqa: PLC0415

    plan = [("japanese", n, "japanese") for n in pick_ja]
    # 対照群は「人間の転写がある人」を優先し、足りなければ番号順に足す
    native = [n for n in en if n in en_txt] + [n for n in en if n not in en_txt]
    plan += [(a.native_lang, n, "native") for n in native[:n_native]]
    sids, stats = [], {"human": 0, "no_human": 0}
    for lang, n, group in plan:
        name = f"{lang}{n}"
        mp3 = root / "mp3" / f"{name}.mp3"
        fetch(idx[f"{name}.mp3"]["download"], mp3)
        trans = None
        if f"{name}.txt" in idx:
            raw = fetch(idx[f"{name}.txt"]["download"], root / "txt" / f"{name}.txt").decode("utf-8", "replace")
            trans = read_transcription(raw)
        stats["human" if trans else "no_human"] += 1
        sid = import_session(store, mp3, f"saa-{name}", group, {"corpus": "saa", "corpus_item": name,
                                                                  "license": "CC BY-NC-SA 4.0"})
        line = {"text": PARAGRAPH}
        if trans:
            line["phones"] = trans
        ref = {"name": name, "group": group, "lang": "en", "source": f"saa:{name}", "transcriber": "saa-transcribers",
               "note": "Speech Accent Archive の精密 IPA 転写（2〜4 名の判定者による。参照であって観察ではない）。CC BY-NC-SA 4.0",
               "lines": [line]}
        rj = root / "ref" / f"{name}.ref.json"
        rj.parent.mkdir(parents=True, exist_ok=True)
        rj.write_text(json.dumps(ref, ensure_ascii=False, indent=1), encoding="utf-8")
        if a.replay:
            from intake.__main__ import main as intake_main  # noqa: PLC0415

            intake_main(["-c", a.config, *sum((["--set", s] for s in a.set), []), "replay", sid, *(["--run", a.run] if a.run else [])])
        ref_to_run.main([sid, str(rj), "--g2p", a.g2p, "--sessions-dir", cfg["sessions_dir"]])
        sids.append(sid)
        print(f"{name}\t{group}\t{sid}\t{'人間の転写あり' if trans else '転写なし（ref_g2p のみ）'}")
    print(json.dumps({"sessions": len(sids), **stats}, ensure_ascii=False))


if __name__ == "__main__":
    main()
