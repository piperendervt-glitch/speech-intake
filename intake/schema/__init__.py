"""発話レコードと「観察表／推察表」の規約。

このパッケージは標準ライブラリだけに依存する（他の intake パッケージを import しない）。
将来、単独で切り出して利用側プロジェクトに配れるようにするため。

- 観察表（Observation table）: 本モジュールが出力する発話レコード ``Utterance`` の列。
- 推察表（Inference table）: 利用側が別ファイルに書く。各行は ``refs`` で発話レコード id を必ず参照する。
  本モジュールは推察表を読みも書きもしない。型と検証関数だけをここに置く。
"""

from .records import (
    PHONES_SOURCES,
    SCHEMA_VERSION,
    Inference,
    Phone,
    SchemaError,
    Utterance,
    VadSegment,
    Word,
    make_utterance_id,
    validate_inference,
    validate_utterance,
)
from .jsonschema import INFERENCE_JSON_SCHEMA, UTTERANCE_JSON_SCHEMA

__all__ = [
    "PHONES_SOURCES",
    "SCHEMA_VERSION",
    "Inference",
    "Phone",
    "SchemaError",
    "Utterance",
    "VadSegment",
    "Word",
    "make_utterance_id",
    "validate_inference",
    "validate_utterance",
    "INFERENCE_JSON_SCHEMA",
    "UTTERANCE_JSON_SCHEMA",
]
