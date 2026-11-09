"""一人で話した収録から「VAD ON ＝ 装着者が話している」の教師データを取り出す（subvox 向け）。

  python tools/export_vad_teacher.py <session> [--run silero] [--out teacher.jsonl]

出力（JSONL、1 行 = 1 区間）:
  {"t0": 1.23, "t1": 3.45, "label": "speech", "vad": "silero-v6", "p": 0.91,
   "t0_unix": 1762671234.56, "t1_unix": 1762671236.78}
  t0/t1 はセッション開始からの秒。t0_unix/t1_unix は録音時の壁時計（マイク録音のセッションのみ。概算）。
  区間と区間の間は「話していない」とみなせる（label を付けた行は出さない）。

前提：一人だけが話した収録であること（他人の声が入っていると、それも speech になる）。
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from intake.session import SessionStore  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("session")
ap.add_argument("--run", default="silero")
ap.add_argument("--sessions-dir", default="sessions")
ap.add_argument("--out")
a = ap.parse_args()
store = SessionStore(a.sessions_dir)
meta = store.read_meta(a.session)
base = meta.get("t0_unix")
vad = store.run_dir(a.session, a.run) / "trace" / "vad.jsonl"
if not vad.exists():
    sys.exit(f"{vad} が無い。先に python -m intake -c config/silero.yaml replay {a.session} を実行する")
out = open(a.out, "w", encoding="utf-8") if a.out else sys.stdout
for line in vad.read_text(encoding="utf-8").splitlines():
    s = json.loads(line)
    row = {"t0": s["t0"], "t1": s["t1"], "label": "speech", "vad": s["vad"], "p": s.get("p")}
    if base is not None:
        row["t0_unix"] = round(base + s["t0"], 3)
        row["t1_unix"] = round(base + s["t1"], 3)
    out.write(json.dumps(row, ensure_ascii=False) + "\n")
