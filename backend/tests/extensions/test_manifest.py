from __future__ import annotations

import pytest

from benchmark.extensions import (
    MANIFEST_FILENAME,
    load_manifest,
    validate_extension_directory,
)


def _write_prompt_only_extension(
    root, *, capability="market.read", version="1.0.0", api_version=1
):
    prompts = root / "prompts"
    prompts.mkdir()
    (prompts / "system.txt").write_text("Static Prompt", encoding="utf-8")
    (prompts / "index.yaml").write_text(
        "api_version: 1\n"
        "prompts:\n"
        "  - id: com.example.system\n"
        "    version: 1.0.0\n"
        "    file: system.txt\n",
        encoding="utf-8",
    )
    (root / MANIFEST_FILENAME).write_text(
        f"api_version: {api_version}\n"
        "id: com.example.extension\n"
        f"version: {version}\n"
        "name: Example\n"
        "components:\n"
        "  prompts:\n"
        "    - directory: prompts\n"
        "      index: prompts/index.yaml\n"
        "capabilities:\n"
        "  requested:\n"
        f"    - {capability}\n",
        encoding="utf-8",
    )


def test_prompt_only_extension_does_not_require_python(tmp_path):
    _write_prompt_only_extension(tmp_path)

    manifest = load_manifest(tmp_path / MANIFEST_FILENAME)
    report = validate_extension_directory(tmp_path)

    assert manifest.python is None
    assert manifest.ref.id == "com.example.extension"
    assert report.valid is True


@pytest.mark.parametrize(
    ("overrides", "validator"),
    [
        ({"capability": "unknown.read"}, "enum"),
        ({"version": "v1"}, "pattern"),
        ({"api_version": 2}, "const"),
    ],
)
def test_manifest_schema_rejects_unknown_capability_and_bad_versions(
    tmp_path, overrides, validator
):
    _write_prompt_only_extension(tmp_path, **overrides)

    report = validate_extension_directory(tmp_path)

    assert report.valid is False
    assert any(issue.validator == validator for issue in report.errors)


def test_semantic_validation_aggregates_duplicate_ids_and_missing_schemas(tmp_path):
    (tmp_path / MANIFEST_FILENAME).write_text(
        "api_version: 1\n"
        "id: com.example.extension\n"
        "version: 1.0.0\n"
        "name: Example\n"
        "python:\n"
        '  requires: ">=3.10"\n'
        "components:\n"
        "  agents:\n"
        "    - id: com.example.agent\n"
        "      factory: fake_module:create\n"
        "      config_schema: schemas/missing-one.json\n"
        "    - id: com.example.agent\n"
        "      factory: fake_module:create\n"
        "      config_schema: schemas/missing-two.json\n",
        encoding="utf-8",
    )

    report = validate_extension_directory(tmp_path)

    assert report.valid is False
    assert [issue.code for issue in report.errors].count(
        "CONFIG_SCHEMA_PATH_INVALID"
    ) == 2
    assert "AGENT_ID_DUPLICATE" in {issue.code for issue in report.errors}


def test_config_schema_cannot_escape_extension_root(tmp_path):
    (tmp_path / MANIFEST_FILENAME).write_text(
        "api_version: 1\n"
        "id: com.example.extension\n"
        "version: 1.0.0\n"
        "name: Example\n"
        "python:\n"
        '  requires: ">=3.10"\n'
        "components:\n"
        "  agents:\n"
        "    - id: com.example.agent\n"
        "      factory: fake_module:create\n"
        "      config_schema: ../outside.json\n",
        encoding="utf-8",
    )

    report = validate_extension_directory(tmp_path)

    assert report.valid is False
    assert report.errors[0].code == "CONFIG_SCHEMA_PATH_INVALID"


def test_static_validation_does_not_import_declared_factory(tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object"}', encoding="utf-8")
    sentinel = tmp_path / "imported.txt"
    (tmp_path / "side_effect_module.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('imported')\n",
        encoding="utf-8",
    )
    (tmp_path / MANIFEST_FILENAME).write_text(
        "api_version: 1\n"
        "id: com.example.extension\n"
        "version: 1.0.0\n"
        "name: Example\n"
        "python:\n"
        '  requires: ">=3.10"\n'
        "components:\n"
        "  agents:\n"
        "    - id: com.example.agent\n"
        "      factory: side_effect_module:create\n"
        "      config_schema: schema.json\n",
        encoding="utf-8",
    )

    assert validate_extension_directory(tmp_path).valid is True
    assert sentinel.exists() is False


def test_duplicate_manifest_key_is_rejected(tmp_path):
    (tmp_path / MANIFEST_FILENAME).write_text(
        "api_version: 1\napi_version: 1\n",
        encoding="utf-8",
    )

    report = validate_extension_directory(tmp_path)

    assert report.valid is False
    assert report.errors[0].code == "MANIFEST_DUPLICATE_KEY"


def test_invalid_config_schema_is_reported(tmp_path):
    (tmp_path / "schema.json").write_text(
        '{"type": "definitely-not-a-json-schema-type"}',
        encoding="utf-8",
    )
    (tmp_path / MANIFEST_FILENAME).write_text(
        "api_version: 1\n"
        "id: com.example.extension\n"
        "version: 1.0.0\n"
        "name: Example\n"
        "python:\n"
        '  requires: ">=3.10"\n'
        "components:\n"
        "  agents:\n"
        "    - id: com.example.agent\n"
        "      factory: fake_module:create\n"
        "      config_schema: schema.json\n",
        encoding="utf-8",
    )

    report = validate_extension_directory(tmp_path)

    assert report.valid is False
    assert {issue.code for issue in report.errors} == {"CONFIG_SCHEMA_INVALID"}


def test_manifest_rejects_non_json_yaml_values(tmp_path):
    _write_prompt_only_extension(tmp_path)
    manifest = tmp_path / MANIFEST_FILENAME
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "name: Example", "name: 2026-07-20"
        ),
        encoding="utf-8",
    )

    report = validate_extension_directory(tmp_path)

    assert report.valid is False
    assert report.errors[0].code == "MANIFEST_NON_JSON_VALUE"
