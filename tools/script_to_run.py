"""合成サンプルの台本（samples/*.script.json）を、閲覧画面で比較できる run「script」として書き出す。

  python tools/script_to_run.py <session> samples/conv2_ja_kokoro.script.json

閲覧画面で 設定 A=script（台本どおりの正解）、設定 B=whisper などと並べると、ずれが目で分かる。
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from intake.schema import Utterance, make_utterance_id  # noqa: E402
from intake.session import SessionStore  # noqa: E402

sid, script = sys.argv[1], json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
store = SessionStore(sys.argv[3] if len(sys.argv) > 3 else "sessions")
rd = store.run_dir(sid, "script")
(rd / "trace").mkdir(parents=True, exist_ok=True)
with (rd / "utterances.jsonl").open("w", encoding="utf-8") as f:
    for i, ln in enumerate(script["lines"]):
        u = Utterance(id=make_utterance_id(i), session=sid, t0=ln["t0"], t1=ln["t1"], text=ln["text"], lang="ja",
                      speaker=f"spk_{ord(ln['speaker']) - 64}", speaker_conf=1.0, speaker_source="script")
        f.write(json.dumps(u.to_dict(), ensure_ascii=False) + "\n")
(rd / "run.json").write_text(json.dumps({
    "session": sid, "run": "script", "source": f"script:{script['name']}",
    "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    "providers": {"vad": "script", "stt": "script", "speaker": "script"},
    "config": {"note": "合成サンプルの台本。台詞を置いた時刻（前後の無音を含む）"},
}, ensure_ascii=False, indent=2), encoding="utf-8")
print(rd)
