"""設定ファイル（YAML 1 つ）の読み込み。provider 名で各段の実装を差し替える。"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "sessions_dir": "sessions",
    "run_name": "default",
    "sample_rate": 16000,
    "capture": {"provider": "sounddevice", "device": None, "block_ms": 32, "latency": None},
    "replay": {"speed": "fast", "chunk_ms": 32},
    "pipeline": {"stt_thread": False},  # true: STT 以降を別スレッドで（VAD の通知が STT を待たない。段10）
    "vad": {"provider": "none"},
    "stt": {"provider": "none"},
    "speaker": {"provider": "none"},
    "phones": {"provider": "none"},
    "sink": {
        "jsonl": True,
        "websocket": {"enabled": False, "host": "127.0.0.1", "port": 8766},
    },
    "viewer": {"host": "127.0.0.1", "port": 8765},
}

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def apply_overrides(cfg: dict[str, Any], sets: list[str]) -> dict[str, Any]:
    """--set vad.threshold=0.6 のような上書き。値は YAML として解釈する。"""
    for item in sets or []:
        key, _, val = item.partition("=")
        if not key or not _:
            raise ValueError(f"--set は key=value の形: {item!r}")
        d = cfg
        parts = key.split(".")
        for p in parts[:-1]:
            d = d.setdefault(p, {})
        d[parts[-1]] = yaml.safe_load(val)
    return cfg


def load_config(path: str | Path | None = None, sets: list[str] | None = None) -> dict[str, Any]:
    cfg = copy.deepcopy(DEFAULTS)
    if path:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        cfg = _merge(cfg, data)
        cfg["_path"] = str(path)
    apply_overrides(cfg, sets or [])
    check_local_only(cfg)
    return cfg


def check_local_only(cfg: dict[str, Any]) -> None:
    """待ち受けは localhost のみ（依頼文 §2）。それ以外の host は起動前に拒否する。"""
    for name, host in (
        ("viewer.host", cfg["viewer"]["host"]),
        ("sink.websocket.host", cfg["sink"]["websocket"]["host"]),
    ):
        if host not in LOCAL_HOSTS:
            raise ValueError(f"{name}={host!r}: 待ち受けは localhost のみ許可（{LOCAL_HOSTS}）")
