"""provider 名 → 実装クラスの対応表。設定ファイルの `<段>.provider` で選ぶ。

重い依存（torch など）は選ばれたときだけ import する。
"""

from __future__ import annotations

import importlib
from typing import Any

PROVIDERS: dict[str, dict[str, str]] = {
    "vad": {
        "none": "intake.vad.base:NoVad",
        "silero": "intake.vad.silero:SileroVad",
    },
    "stt": {
        "none": "intake.stt.base:NoStt",
        "faster_whisper": "intake.stt.faster_whisper:FasterWhisperStt",
    },
    "speaker": {
        "none": "intake.speaker.base:NoSpeaker",
        "acoustic": "intake.speaker.acoustic:PyannoteSpeaker",
        "wearer": "intake.speaker.wearer:WearerSpeaker",
    },
}


def create(stage: str, cfg: dict[str, Any], **kw: Any) -> Any:
    sec = dict(cfg.get(stage) or {})
    name = sec.pop("provider", "none")
    try:
        target = PROVIDERS[stage][name]
    except KeyError:
        raise ValueError(f"{stage}.provider={name!r} は未登録。候補: {sorted(PROVIDERS[stage])}") from None
    mod, cls = target.split(":")
    return getattr(importlib.import_module(mod), cls)(**sec, **kw)
