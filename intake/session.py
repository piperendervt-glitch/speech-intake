"""セッションの置き場所。

sessions/<session>/
  audio.wav                16 kHz mono PCM16。時刻の唯一の基準
  session.json             録音のメタ情報（開始時刻・入口・状態）
  runs/<run>/run.json      その流し直しに使った設定のスナップショット
  runs/<run>/utterances.jsonl   観察表（発話レコード）
  runs/<run>/trace/<stage>.jsonl 段ごとの記録（VAD 区間など）。閲覧画面での確認用で、観察表ではない
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

_RUN_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_SESSION_RE = re.compile(r"^\d{8}_\d{6}(?:_[A-Za-z0-9-]+)?$")


def new_session_id(now: datetime | None = None, suffix: str | None = None) -> str:
    sid = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return f"{sid}_{suffix}" if suffix else sid


def check_session_id(sid: str) -> str:
    if not _SESSION_RE.match(sid):
        raise ValueError(f"session id が不正: {sid!r}")
    return sid


def check_run_name(run: str) -> str:
    if not _RUN_RE.match(run):
        raise ValueError(f"run 名は英数字と . _ - のみ: {run!r}")
    return run


class SessionStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def dir(self, sid: str) -> Path:
        return self.root / check_session_id(sid)

    def audio_path(self, sid: str) -> Path:
        return self.dir(sid) / "audio.wav"

    def meta_path(self, sid: str) -> Path:
        return self.dir(sid) / "session.json"

    def run_dir(self, sid: str, run: str) -> Path:
        return self.dir(sid) / "runs" / check_run_name(run)

    def create(self, sid: str, meta: dict[str, Any]) -> Path:
        d = self.dir(sid)
        if d.exists():
            raise FileExistsError(f"セッションが既にある: {d}")
        d.mkdir(parents=True)
        self.write_meta(sid, {"session": sid, **meta})
        return d

    def read_meta(self, sid: str) -> dict[str, Any]:
        p = self.meta_path(sid)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"session": sid}

    def write_meta(self, sid: str, meta: dict[str, Any]) -> None:
        p = self.meta_path(sid)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)

    def update_meta(self, sid: str, **kw: Any) -> dict[str, Any]:
        m = self.read_meta(sid)
        m.update(kw)
        self.write_meta(sid, m)
        return m

    def list_sessions(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(
            (p.name for p in self.root.iterdir() if p.is_dir() and _SESSION_RE.match(p.name)),
            reverse=True,
        )

    def list_runs(self, sid: str) -> list[str]:
        d = self.dir(sid) / "runs"
        if not d.exists():
            return []
        return sorted(p.name for p in d.iterdir() if p.is_dir() and _RUN_RE.match(p.name))
