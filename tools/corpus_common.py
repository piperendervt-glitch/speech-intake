"""公開コーパスの取り込みで共通に使うもの（取得・キャッシュ・セッション化）。

取得したものはすべて data/（git に入らない）に置く。話者を特定しようとしない（ファイル名の番号以外は使わない）。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

UA = "speech-intake-corpus-import/0.2 (research; local use)"


def fetch(url: str, dst: Path | None = None, retries: int = 4) -> bytes:
    """URL を取得する。dst があり既にあれば読むだけ（2 回目以降はネットに出ない）。"""
    if dst is not None and dst.exists() and dst.stat().st_size > 0:
        return dst.read_bytes()
    last = None
    for k in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
                data = r.read()
            if dst is not None:
                dst.parent.mkdir(parents=True, exist_ok=True)
                tmp = dst.with_suffix(dst.suffix + ".part")
                tmp.write_bytes(data)
                tmp.replace(dst)
            return data
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 ** (k + 1))
    raise RuntimeError(f"取得できない: {url}: {last}")


def fetch_json(url: str) -> dict:
    return json.loads(fetch(url).decode("utf-8"))


def import_session(store, audio: Path, label: str, group: str, extra_meta: dict) -> str:
    """音声をセッションにする（既に同じ label のセッションがあればそれを返す）。"""
    from intake.replay import import_audio  # noqa: PLC0415

    for sid in store.list_sessions():
        if sid.endswith("_" + label) or store.read_meta(sid).get("corpus_label") == label:
            return sid
    sid = import_audio(store, audio, label=label)
    store.update_meta(sid, group=group, corpus_label=label, **extra_meta)
    return sid


def quote(s: str) -> str:
    return urllib.parse.quote(s, safe="")
