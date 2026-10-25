from songspec_analyzer.contract import SCHEMA_PATH, Analysis, schema_text


def test_committed_schema_is_current():
    assert SCHEMA_PATH.exists(), "run: python -m songspec_analyzer.contract"
    assert SCHEMA_PATH.read_text(encoding="utf-8") == schema_text(), \
        "contract/analysis.schema.json is stale - run: python -m songspec_analyzer.contract"


def test_example_validates():
    import json
    ex = json.loads((SCHEMA_PATH.parent / "examples" / "complete.json").read_text(encoding="utf-8"))
    Analysis.model_validate(ex)
