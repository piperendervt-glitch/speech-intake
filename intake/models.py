"""モデルの取得（python -m intake models）。取得先は models/（git に入らない）。

  speaker  sherpa-onnx の話者分離モデル（GitHub k2-fsa/sherpa-onnx の releases から。約 40 MB）
  whisper  faster-whisper のモデル（HuggingFace Systran/faster-whisper-* から。large-v3 は約 3 GB。置き場所は HuggingFace の既定キャッシュ）
           ※ 何もしなくても初回の実行時に自動で取得される。先に取っておきたいときだけ使う。

取得した各ファイルの大きさと sha256 を models/MANIFEST.txt に追記する（出典の記録）。
"""

from __future__ import annotations

import hashlib
import tarfile
import time
import urllib.error
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
    try:
        urllib.request.urlretrieve(url, tmp)
    except urllib.error.HTTPError as e:
        # releases/download が 5xx を返すことがある。GitHub API の asset URL から取り直す
        print(f"  {e.code} のため GitHub API 経由で取り直す")
        _get_via_api(url, tmp)
    tmp.replace(dst)
    with (root / "MANIFEST.txt").open("a", encoding="utf-8") as m:
        m.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{url}\t{dst.stat().st_size}\tsha256={_sha(dst)}\n")
    if name.endswith(".tar.bz2"):
        with tarfile.open(dst) as t:
            t.extractall(dst_dir, filter="data")
        dst.unlink()


def _get_via_api(url: str, dst: Path) -> None:
    import json  # noqa: PLC0415

    # https://github.com/<owner>/<repo>/releases/download/<tag>/<name>
    parts = url.split("/")
    owner, repo, tag, name = parts[3], parts[4], parts[7], parts[8]
    api = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}"
    with urllib.request.urlopen(urllib.request.Request(api, headers={"Accept": "application/vnd.github+json"})) as r:
        rel = json.load(r)
    asset = next((a for a in rel["assets"] if a["name"] == name), None)
    if asset is None:
        raise RuntimeError(f"{tag} に {name} が無い")
    req = urllib.request.Request(asset["url"], headers={"Accept": "application/octet-stream"})
    with urllib.request.urlopen(req) as r, dst.open("wb") as f:
        while b := r.read(1 << 20):
            f.write(b)


def fetch_speaker(root: Path) -> None:
    for url, sub in SPEAKER_FILES:
        _get(url, root / sub, root)


def fetch_whisper(name: str, root: Path) -> None:
    from faster_whisper.utils import download_model  # noqa: PLC0415

    path = download_model(name)  # STT と同じ HuggingFace の既定キャッシュに置く
    print(f"取得済み: {path}")
    with (root / "MANIFEST.txt").open("a", encoding="utf-8") as m:
        m.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\tfaster-whisper:{name}\t{path}\n")
