"""JSON Schema をファイルに書き出す: python -m intake.schema [出力先ディレクトリ]"""

import json
import sys
from pathlib import Path

from .jsonschema import INFERENCE_JSON_SCHEMA, UTTERANCE_JSON_SCHEMA

out = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/schema")
out.mkdir(parents=True, exist_ok=True)
for name, s in (("utterance.schema.json", UTTERANCE_JSON_SCHEMA), ("inference.schema.json", INFERENCE_JSON_SCHEMA)):
    (out / name).write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(out / name)
