"""話者分離の閾値（speaker.threshold）を何通りか試し、正解の台本と比べた表を出す（STT は回さないので速い）。

  python tools/sweep_speaker.py <session> <台本.script.json> [--thresholds 0.5,0.6,0.7,0.8,0.9,1.0] [--write-runs]

各閾値について:
  人数        話者分離が出した話者の数（台本の人数と比べる）
  取り違え    台本の各行に「いちばん長く重なった話者ラベル」を付け、そのラベルが主に担当している台本の人と違う行の数
  分割        台本の 1 人が、1 秒以上ずつ複数のラベルに分かれた数（同じ人が別人として立った数）
--write-runs を付けると、閾値ごとに run「spk_t<閾値>」を作る（文字は空、話者区間だけ）。閲覧画面で台本と並べて見られる。
数値はあくまで目安。最後は閲覧画面で見て決める。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from intake.schema import Utterance, make_utterance_id  # noqa: E402
from intake.session import SessionStore  # noqa: E402
from intake.speaker.acoustic import SherpaSpeaker  # noqa: E402


def overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def evaluate(turns, lines):
    # ラベル × 台本の人 の重なり秒数
    ov = defaultdict(float)
    for a, b, lbl in turns:
        for ln in lines:
            ov[(lbl, ln["speaker"])] += overlap(a, b, ln["t0"], ln["t1"])
    labels = sorted({l for _, _, l in turns})
    owner = {l: max({s for _, s in ov} or {""}, key=lambda s: ov[(l, s)]) for l in labels}
    wrong = 0
    for ln in lines:
        per = defaultdict(float)
        for a, b, lbl in turns:
            per[lbl] += overlap(a, b, ln["t0"], ln["t1"])
        if not per or max(per.values()) == 0:
            wrong += 1
            continue
        top = max(per, key=per.get)
        wrong += owner[top] != ln["speaker"]
    split = 0
    for s in {ln["speaker"] for ln in lines}:
        split += max(0, sum(1 for l in labels if ov[(l, s)] >= 1.0) - 1)
    return len(labels), wrong, split


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session")
    ap.add_argument("script")
    ap.add_argument("--thresholds", default="0.5,0.6,0.7,0.8,0.9,1.0,1.1")
    ap.add_argument("--sessions-dir", default="sessions")
    ap.add_argument("--write-runs", action="store_true")
    a = ap.parse_args()
    store = SessionStore(a.sessions_dir)
    audio, sr = sf.read(str(store.audio_path(a.session)), dtype="float32")
    lines = json.loads(Path(a.script).read_text(encoding="utf-8"))["lines"]
    n_true = len({ln["speaker"] for ln in lines})
    print(f"# {a.session}  台本の人数 {n_true}  行数 {len(lines)}  長さ {len(audio) / sr:.1f}s")
    print("閾値\t人数\t取り違え\t分割")
    for t in [float(x) for x in a.thresholds.split(",")]:
        sp = SherpaSpeaker(threshold=t)
        turns = sp.diarize(audio, sr)
        n, wrong, split = evaluate(turns, lines)
        print(f"{t:.2f}\t{n}\t{wrong}/{len(lines)}\t{split}")
        if a.write_runs:
            run = f"spk_t{t:.2f}"
            rd = store.run_dir(a.session, run)
            (rd / "trace").mkdir(parents=True, exist_ok=True)
            names = {}
            for _, _, l in sorted(turns):
                names.setdefault(l, f"spk_{len(names) + 1}")
            with (rd / "trace" / "speaker.jsonl").open("w", encoding="utf-8") as f:
                for x0, x1, l in sorted(turns):
                    f.write(json.dumps({"t0": round(x0, 3), "t1": round(x1, 3), "speaker": names[l], "by": sp.name}) + "\n")
            with (rd / "utterances.jsonl").open("w", encoding="utf-8") as f:
                for i, (x0, x1, l) in enumerate(sorted(turns)):
                    u = Utterance(id=make_utterance_id(i), session=a.session, t0=x0, t1=x1, speaker=names[l],
                                  speaker_conf=1.0, speaker_source="acoustic")
                    f.write(json.dumps(u.to_dict(), ensure_ascii=False) + "\n")
            (rd / "run.json").write_text(json.dumps({
                "session": a.session, "run": run, "source": "sweep_speaker",
                "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "providers": {"vad": "none", "stt": "none", "speaker": sp.name},
                "config": {"speaker": {"provider": "acoustic", "threshold": t}},
            }, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
