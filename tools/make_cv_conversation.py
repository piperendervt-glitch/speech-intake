"""公開の読み上げ音声（Common Voice 日本語 / JVS）をつないで「疑似会話」を作り、正解の台本を付ける。

  python tools/make_cv_conversation.py <Common Voice のフォルダ> [--speakers 3] [--turns 16] [--seed 1]
  python tools/make_cv_conversation.py <JVS のフォルダ> --format jvs --speakers 3 --turns 16

Common Voice: validated.tsv と clips/ がある所（例 cv-corpus-27.0-…/ja）。CC0。
JVS: jvs001〜jvs100 のフォルダがある所（例 jvs_ver1/jvs_ver1）。parallel100 と nonpara30 の読み上げを使う。
     利用条件は配布元（東京大学 猿渡研究室）に従う。
出力（既定 data/cv_conv/。git に入らない）:
  cv_conv_<seed>.wav          16 kHz mono
  cv_conv_<seed>.script.json  台本（話者 A/B/C…・文・置いた時刻）。tools/script_to_run.py にそのまま渡せる

作り方: validated.tsv から発話数の多い人を --speakers 人選び、その人たちの音声を交互に並べる。
間（ポーズ）は 0.25〜1.2 秒でランダム。--overlap を付けると一部の番で前の人に少し重ねる。
各音声の前後の無音は切り詰めない（Common Voice の録音そのまま）。台本の t0/t1 はクリップを置いた位置。

Common Voice の利用条件により、話者を特定しようとしないこと。作った音声は公開リポジトリに入れないこと。
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 16000


def load_clip(path: Path) -> np.ndarray:
    try:
        x, sr = sf.read(str(path), dtype="float32", always_2d=True)
        x = x.mean(axis=1)
    except Exception:  # noqa: BLE001  libsndfile が mp3 を読めない環境
        if not shutil.which("ffmpeg"):
            raise
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "a.wav"
            subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(path), "-ac", "1", "-ar", str(SR), str(out)], check=True)
            return sf.read(str(out), dtype="float32")[0]
    if sr != SR:
        n = int(len(x) * SR / sr)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
    return x


def load_cv(root: Path) -> dict[str, list[dict]]:
    rows: dict[str, list[dict]] = defaultdict(list)
    with (root / "validated.tsv").open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
            if r.get("path") and r.get("sentence"):
                rows[r["client_id"]].append({**r, "file": str(root / "clips" / r["path"])})
    return rows


def load_jvs(root: Path) -> dict[str, list[dict]]:
    """jvsNNN/{parallel100,nonpara30}/transcripts_utf8.txt（"名前:文"）と wav24kHz16bit/名前.wav。"""
    rows: dict[str, list[dict]] = defaultdict(list)
    for spk in sorted(p for p in root.iterdir() if p.is_dir() and p.name.startswith("jvs")):
        for sub in ("parallel100", "nonpara30"):
            tr = spk / sub / "transcripts_utf8.txt"
            if not tr.exists():
                continue
            for line in tr.read_text(encoding="utf-8").splitlines():
                name, _, text = line.partition(":")
                wav = spk / sub / "wav24kHz16bit" / f"{name}.wav"
                if text and wav.exists():
                    rows[spk.name].append({"client_id": spk.name, "path": f"{spk.name}/{sub}/{name}.wav",
                                           "sentence": text, "file": str(wav)})
    if not rows:
        raise SystemExit(f"{root} に jvsNNN/…/transcripts_utf8.txt が見つからない")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cv_dir")
    ap.add_argument("--speakers", type=int, default=3)
    ap.add_argument("--turns", type=int, default=16)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--overlap", action="store_true", help="一部の番で前の人に 0.2〜0.6 秒重ねる")
    ap.add_argument("--out", default="data/cv_conv")
    ap.add_argument("--format", choices=["cv", "jvs"], default="cv")
    a = ap.parse_args()

    root = Path(a.cv_dir)
    rows = load_jvs(root) if a.format == "jvs" else load_cv(root)
    rng = random.Random(a.seed)
    cands = [c for c, rs in rows.items() if len(rs) >= a.turns]
    if len(cands) < a.speakers:
        cands = sorted(rows, key=lambda c: -len(rows[c]))
    people = rng.sample(sorted(cands), a.speakers)
    labels = [chr(65 + i) for i in range(a.speakers)]

    track = np.zeros(int(0.5 * SR), dtype=np.float32)
    cursor = 0.5
    lines = []
    prev = None
    for _ in range(a.turns):
        i = rng.choice([k for k in range(a.speakers) if k != prev])
        prev = i
        r = rng.choice(rows[people[i]])
        x = load_clip(Path(r["file"]))
        x = 0.7 * x / (np.max(np.abs(x)) + 1e-9)
        gap = rng.uniform(0.25, 1.2)
        if a.overlap and lines and rng.random() < 0.25:
            gap = -rng.uniform(0.2, 0.6)
        t0 = max(0.0, cursor + gap)
        s = int(t0 * SR)
        if s + len(x) > len(track):
            track = np.concatenate([track, np.zeros(s + len(x) - len(track), dtype=np.float32)])
        track[s : s + len(x)] += x
        t1 = t0 + len(x) / SR
        lines.append({"speaker": labels[i], "text": r["sentence"], "t0": round(t0, 3), "t1": round(t1, 3), "clip": r["path"], "corpus_speaker": r["client_id"] if a.format == "jvs" else None})
        cursor = max(cursor, t1)
    track = np.clip(np.concatenate([track, np.zeros(int(0.8 * SR), dtype=np.float32)]), -1, 1)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{a.format}_conv_{a.seed}"
    sf.write(out / f"{stem}.wav", track, SR, subtype="PCM_16")
    meta = {"name": stem, "engine": "common-voice" if a.format == "cv" else "jvs", "source": str(root), "speakers": a.speakers, "seed": a.seed,
            "note": "公開コーパスの読み上げをつないだ疑似会話。t0/t1 はクリップを置いた位置（前後の無音を含む）",
            "lines": lines}
    (out / f"{stem}.script.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out / f"{stem}.wav", f"{len(track) / SR:.1f}s", f"{a.speakers} 人 / {a.turns} 番")


if __name__ == "__main__":
    main()
