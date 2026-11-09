import pytest

from intake.schema import Inference, SchemaError, Utterance, Word, validate_inference, validate_utterance


def test_utterance_roundtrip():
    u = Utterance(id="utt_000123", session="20261109_160000", t0=12.34, t1=14.87, text="先月の",
                  lang="ja", speaker="spk_1", speaker_conf=0.82, speaker_source="acoustic",
                  words=[Word("先月", 12.34, 12.71)], vad="silero-v5", stt="faster-whisper-large-v3")
    d = u.to_dict()
    assert Utterance.from_dict(d).to_dict() == d


@pytest.mark.parametrize("bad", [
    {"id": "x1", "session": "20261109_160000", "t0": 0, "t1": 1},
    {"id": "utt_000001", "session": "today", "t0": 0, "t1": 1},
    {"id": "utt_000001", "session": "20261109_160000", "t0": 2, "t1": 1},
    {"id": "utt_000001", "session": "20261109_160000", "t0": 0, "t1": 1, "speaker_source": "guess"},
])
def test_utterance_rejects(bad):
    with pytest.raises(SchemaError):
        validate_utterance(bad)


def test_inference_requires_refs():
    with pytest.raises(SchemaError):
        validate_inference({"id": "inf_1", "refs": [], "kind": "intent", "value": "x", "by": "llm"})
    Inference(id="inf_1", refs=["utt_000123"], kind="intent", value="集計依頼", by="test").to_dict()


def test_schema_package_has_no_internal_deps():
    import ast
    import pathlib

    for p in pathlib.Path("intake/schema").glob("*.py"):
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                assert not node.module.startswith("intake"), f"{p}: {node.module}"
            if isinstance(node, ast.ImportFrom) and node.level >= 2:
                raise AssertionError(f"{p}: 親パッケージを import している")
