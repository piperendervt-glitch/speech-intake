from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..audio import AudioChunk
from ..schema import Utterance


class JsonlSink:
    """runs/<run>/utterances.jsonl に発話レコードを 1 行ずつ追記する（観察表）。

    trace/<stage>.jsonl には段ごとの記録（VAD 区間など）を書く。これは閲覧画面で確かめるためのもので観察表ではない。
    revise() は speaker の付け直しなどで列全体が確定し直したときに、utterances.jsonl を書き直す。
    """

    def __init__(self, run_dir: str | Path):
        self.dir = Path(run_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "trace").mkdir(exist_ok=True)
        self.path = self.dir / "utterances.jsonl"
        self._f = self.path.open("w", encoding="utf-8")
        self._traces: dict[str, Any] = {}

    def start(self, info: dict[str, Any]) -> None:
        (self.dir / "run.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

    def audio(self, chunk: AudioChunk) -> None:  # noqa: ARG002
        return None

    def trace(self, stage: str, obj: dict[str, Any]) -> None:
        f = self._traces.get(stage)
        if f is None:
            f = self._traces[stage] = (self.dir / "trace" / f"{stage}.jsonl").open("w", encoding="utf-8")
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        f.flush()

    def emit(self, utt: Utterance) -> None:
        self._f.write(json.dumps(utt.to_dict(), ensure_ascii=False) + "\n")
        self._f.flush()

    def revise(self, utts: list[Utterance]) -> None:
        self._f.close()
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for u in utts:
                f.write(json.dumps(u.to_dict(), ensure_ascii=False) + "\n")
        tmp.replace(self.path)
        self._f = self.path.open("a", encoding="utf-8")

    def end(self, stats: dict[str, Any]) -> None:
        info = json.loads((self.dir / "run.json").read_text(encoding="utf-8"))
        info["stats"] = stats
        (self.dir / "run.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

    def close(self) -> None:
        self._f.close()
        for f in self._traces.values():
            f.close()
