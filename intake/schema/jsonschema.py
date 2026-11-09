"""JSON Schema（draft 2020-12）。利用側が他言語で検証するとき用。

`python -m intake.schema` で docs/schema/*.json に書き出せる。
"""

UTTERANCE_JSON_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "speech-intake/utterance-0.1.json",
    "title": "Utterance (観察表の 1 行)",
    "type": "object",
    "required": ["id", "session", "t0", "t1"],
    "properties": {
        "id": {"type": "string", "pattern": "^utt_[0-9]{6,}$"},
        "session": {"type": "string", "pattern": "^[0-9]{8}_[0-9]{6}(_[A-Za-z0-9-]+)?$"},
        "t0": {"type": "number", "minimum": 0, "description": "セッション開始からの秒"},
        "t1": {"type": "number", "minimum": 0, "description": "セッション開始からの秒"},
        "text": {"type": "string"},
        "lang": {"type": ["string", "null"]},
        "speaker": {"type": ["string", "null"]},
        "speaker_conf": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "speaker_source": {"enum": ["acoustic", "wearer", "face", "none"]},
        "words": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["w", "t0", "t1"],
                "properties": {
                    "w": {"type": "string"},
                    "t0": {"type": "number", "minimum": 0},
                    "t1": {"type": "number", "minimum": 0},
                    "p": {"type": "number"},
                },
            },
        },
        "vad": {"type": ["string", "null"]},
        "stt": {"type": ["string", "null"]},
    },
}

INFERENCE_JSON_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "speech-intake/inference-0.1.json",
    "title": "Inference (推察表の 1 行。利用側が書く)",
    "type": "object",
    "required": ["id", "refs", "kind", "value", "by"],
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "refs": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "pattern": "^utt_[0-9]{6,}$"},
            "description": "根拠にした発話レコード id。必須。",
        },
        "kind": {"type": "string", "minLength": 1},
        "value": {},
        "by": {"type": "string", "minLength": 1},
        "conf": {"type": ["number", "null"]},
        "note": {"type": ["string", "null"]},
    },
}
