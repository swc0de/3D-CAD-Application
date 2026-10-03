"""The checked-in schema and reference docs must match the op registry."""

import json

import jsonschema

from pymodeler.ops.registry import PARAM_TYPES, all_ops
from pymodeler.script.docs import DOCS_PATH, generate_docs
from pymodeler.script.schema import SCHEMA_PATH, build_schema, param_schema, schema_text


def test_schema_file_is_up_to_date() -> None:
    assert SCHEMA_PATH.read_text(encoding="utf-8") == schema_text(), "run: python -m pymodeler docs"


def test_docs_file_is_up_to_date() -> None:
    assert DOCS_PATH.read_text(encoding="utf-8") == generate_docs(), "run: python -m pymodeler docs"


def test_schema_is_valid_and_covers_every_op() -> None:
    schema = build_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    for spec in all_ops():
        assert f"op_{spec.name}" in schema["$defs"]
        for p in spec.params:
            assert p.type in PARAM_TYPES
            param_schema(p)
        assert f"### `{spec.name}`" in generate_docs()


def test_schema_accepts_examples_directory_style_script() -> None:
    script = json.loads('{"version": 1, "steps": [{"op": "box", "id": "b", "size": [1, 2, 3]},'
                        '{"op": "paint", "target": "b", "material": null}]}')
    jsonschema.validate(script, build_schema())


def test_claude_md_ops_list_is_up_to_date() -> None:
    from pymodeler.script.docs import CLAUDE_PATH, OPS_BEGIN, updated_claude_md

    text = CLAUDE_PATH.read_text(encoding="utf-8")
    assert OPS_BEGIN in text
    assert updated_claude_md(text) == text, "run: python -m pymodeler docs"
