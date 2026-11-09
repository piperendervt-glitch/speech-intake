"""モデルの取得（python -m intake models）。取得先は models/（git に入らない）。

  speaker  sherpa-onnx の話者分離モデル（GitHub k2-fsa/sherpa-onnx の releases から。約 40 MB）
  whisper  faster-whisper のモデル（HuggingFace Systran/faster-whisper-* から。large-v3 は約 3 GB）
           ※ 何もしなくても初回の実行時に自動で取得される。先に取っておきたいときだけ使う。

取得した各ファイルの大きさと sha256 を models/MANIFEST.txt に追記する（出典の記録）。
"""

from __future__ import annotations

import hashlib
import tarfile
import time
import urllib.request
from pathlib import Path

SHERPA = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
SPEAKER_FILES = [
    (f"{SHERPA}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2", "speaker/"),
    (f"{SHERPA}/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx", "speaker/"),
]


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _get(url: str, dst_dir: Path, root: Path) -> None:
    dst_dir.mkdir(parents=True, exist_ok=True)
    name = url.rsplit("/", 1)[-1]
    dst = dst_dir / name
    if dst.exists() or (name.endswith(".tar.bz2") and (dst_dir / name[: -len(".tar.bz2")]).exists()):
        print(f"既にある: {dst}")
        return
    print(f"取得中: {url}")
    tmp = dst.with_suffix(dst.suffix + ".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.replace(dst)
    with (root / "MANIFEST.txt").open("a", encoding="utf-8") as m:
        m.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{url}\t{dst.stat().st_size}\tsha256={_sha(dst)}\n")
    if name.endswith(".tar.bz2"):
        with tarfile.open(dst) as t:
            t.extractall(dst_dir, filter="data")
        dst.unlink()


def fetch_speaker(root: Path) -> None:
    for url, sub in SPEAKER_FILES:
        _get(url, root / sub, root)


def fetch_whisper(name: str, root: Path) -> None:
    from faster_whisper.utils import download_model  # noqa: PLC0415

    path = download_model(name, cache_dir=str(root / "whisper"))
    print(f"取得済み: {path}")
    with (root / "MANIFEST.txt").open("a", encoding="utf-8") as m:
        m.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\tfaster-whisper:{name}\t{path}\n")
