"""JSON Schema（draft 2020-12）。利用側が他言語で検証するとき用。

`python -m intake.schema` で docs/schema/*.json に書き出せる。
"""

UTTERANCE_JSON_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "speech-intake/utterance-0.2.json",
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
        "speaker_source": {"enum": ["acoustic", "wearer", "face", "script", "none"]},
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
        "phones": {
            "type": "array",
            "description": "schema 0.2。どう発音されたか（音素列）。欄が無ければ空として読む",
            "items": {
                "type": "object",
                "required": ["ph"],
                "properties": {
                    "ph": {"type": "string", "minLength": 1, "description": "IPA 文字列"},
                    "t0": {"type": ["number", "null"], "minimum": 0, "description": "セッション秒。参照（human/g2p）では null のことがある"},
                    "t1": {"type": ["number", "null"], "minimum": 0},
                    "p": {"type": "number", "description": "認識器が出した確率の転記"},
                    "w": {"type": "integer", "minimum": 0, "description": "参照だけ：text を空白で切った何番目の語か"},
                },
            },
        },
        "phones_model": {"type": ["string", "null"], "description": "音素認識器の名前（例 wav2vec2-lv-60-espeak-cv-ft）"},
        "phones_source": {
            "enum": ["model", "human", "g2p", "none"],
            "description": "model=認識器（観察）。human=人間の転写、g2p=辞書変換（どちらも参照 run だけに入る）",
        },
    },
}

INFERENCE_JSON_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "speech-intake/inference-0.2.json",
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
