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
    "capture": {
        "provider": "sounddevice",  # sounddevice（マイク）/ ja1_udp（subvocal-robot の airpods.bat が送る JA1 行。段11）
        "device": None,          # 番号か名前の一部（段11）。"AirPods@MME" のように @ でホスト API も絞れる
        "hostapi": None,         # 名前で選ぶときにホスト API（MME / WASAPI など）を絞る（名前の一部）
        "device_rate": None,     # 機器を開くレート。null = 16 kHz で開き、断られたら機器の既定で開いて 16 kHz に直す / native / 数
        "stall_s": 2.0,          # 録音中にこの秒数音が届かなければ機器が消えたとみなして止める（それまでは保存）
        "latency_ms": 0,         # 入力の遅れ（Bluetooth など。tools/latency_pair.py で測る）。通知の t_said_unix の補正に使う
        "block_ms": 32,
        "latency": None,
        "udp": {"host": "127.0.0.1", "port": 9750, "source": None},  # ja1_udp の待ち受け（localhost のみ）
    },
    "replay": {"speed": "fast", "chunk_ms": 32},
    "pipeline": {
        "stt_thread": False,  # true: STT 以降を別スレッドで（VAD の通知が STT を待たない。段10）
        # 暫定の文字（partial。段11）。区間が開いている間に、そこまでの音声を STT に通して配る。stt_thread: true のときだけ動く
        #   every_ms    話している間、この間隔で出す（0 = 出さない）
        #   silence_ms  黙ってからこの長さで 1 回出す（null = 出さない。min_silence_ms より短くする）
        #   min_ms      区間がこの長さに届くまでは出さない
        "partial": {"every_ms": 0, "silence_ms": None, "min_ms": 300},
    },
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
        ("capture.udp.host", ((cfg.get("capture") or {}).get("udp") or {}).get("host", "127.0.0.1")),
    ):
        if host not in LOCAL_HOSTS:
            raise ValueError(f"{name}={host!r}: 待ち受けは localhost のみ許可（{LOCAL_HOSTS}）")
