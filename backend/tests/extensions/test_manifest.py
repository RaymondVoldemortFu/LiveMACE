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


def test_python_manifest_requires_canonical_entrypoint(tmp_path):
    (tmp_path / MANIFEST_FILENAME).write_text(
        "api_version: 1\n"
        "id: com.example.extension\n"
        "version: 1.0.0\n"
        "name: Example\n"
        "python:\n"
        '  requires: ">=3.10"\n'
        "components:\n"
        "  tools:\n"
        "    - provider: fake_module:create_provider\n",
        encoding="utf-8",
    )

    report = validate_extension_directory(tmp_path)

    assert report.valid is False
    assert any(issue.path == "python" for issue in report.errors)


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
        "  entrypoint: fake_module:extension\n"
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
        "  entrypoint: fake_module:extension\n"
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
        "  entrypoint: side_effect_module:extension\n"
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
        "  entrypoint: fake_module:extension\n"
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


def _write_two_prompt_directory_extension(
    root,
    *,
    second_prompt_id="com.example.other",
    first_profile=None,
    second_profile=None,
):
    def _index_text(prompt_id, profile):
        lines = [
            "api_version: 1",
            "prompts:",
            f"  - id: {prompt_id}",
            "    version: 1.0.0",
            "    file: system.txt",
        ]
        if profile is not None:
            profile_id, profile_version = profile
            lines += [
                "profiles:",
                f"  - id: {profile_id}",
                f"    version: {profile_version}",
                "    slots:",
                "      system:",
                f"        prompt_id: {prompt_id}",
            ]
        return "\n".join(lines) + "\n"

    for name, prompt_id, profile in (
        ("prompts_a", "com.example.system", first_profile),
        ("prompts_b", second_prompt_id, second_profile),
    ):
        directory = root / name
        directory.mkdir()
        (directory / "system.txt").write_text("Static Prompt", encoding="utf-8")
        (directory / "index.yaml").write_text(
            _index_text(prompt_id, profile), encoding="utf-8"
        )

    (root / MANIFEST_FILENAME).write_text(
        "api_version: 1\n"
        "id: com.example.extension\n"
        "version: 1.0.0\n"
        "name: Example\n"
        "components:\n"
        "  prompts:\n"
        "    - directory: prompts_a\n"
        "      index: prompts_a/index.yaml\n"
        "    - directory: prompts_b\n"
        "      index: prompts_b/index.yaml\n",
        encoding="utf-8",
    )


def test_distinct_prompt_ids_across_directories_are_valid(tmp_path):
    _write_two_prompt_directory_extension(tmp_path)

    assert validate_extension_directory(tmp_path).valid is True


def test_duplicate_prompt_id_across_directories_is_rejected(tmp_path):
    _write_two_prompt_directory_extension(
        tmp_path, second_prompt_id="com.example.system"
    )

    report = validate_extension_directory(tmp_path)

    assert report.valid is False
    conflicts = [
        issue
        for issue in report.errors
        if issue.code == "PROMPT_ID_CROSS_DIRECTORY_CONFLICT"
    ]
    assert len(conflicts) == 1
    assert "prompts_a" in conflicts[0].message
    assert "prompts_b" in conflicts[0].message


def test_duplicate_prompt_profile_across_directories_is_rejected(tmp_path):
    _write_two_prompt_directory_extension(
        tmp_path,
        first_profile=("com.example.profile", "1.0.0"),
        second_profile=("com.example.profile", "1.0.0"),
    )

    report = validate_extension_directory(tmp_path)

    assert report.valid is False
    conflicts = [
        issue
        for issue in report.errors
        if issue.code == "PROMPT_PROFILE_CROSS_DIRECTORY_CONFLICT"
    ]
    assert len(conflicts) == 1
    assert "prompts_a" in conflicts[0].message
    assert "prompts_b" in conflicts[0].message


def test_same_profile_id_with_different_versions_is_valid(tmp_path):
    _write_two_prompt_directory_extension(
        tmp_path,
        first_profile=("com.example.profile", "1.0.0"),
        second_profile=("com.example.profile", "2.0.0"),
    )

    assert validate_extension_directory(tmp_path).valid is True


def test_manifest_symlink_escaping_extension_root_is_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    real_manifest = outside / "real-manifest.yaml"
    real_manifest.write_text(
        "api_version: 1\n"
        "id: com.example.extension\n"
        "version: 1.0.0\n"
        "name: Example\n",
        encoding="utf-8",
    )
    extension_root = tmp_path / "extension"
    extension_root.mkdir()
    (extension_root / MANIFEST_FILENAME).symlink_to(real_manifest)

    report = validate_extension_directory(extension_root)

    assert report.valid is False
    assert report.errors[0].code == "MANIFEST_PATH_INVALID"


def test_manifest_symlink_inside_extension_root_is_allowed(tmp_path):
    _write_prompt_only_extension(tmp_path)
    real_manifest = tmp_path / "real-manifest.yaml"
    (tmp_path / MANIFEST_FILENAME).rename(real_manifest)
    (tmp_path / MANIFEST_FILENAME).symlink_to(real_manifest)

    assert validate_extension_directory(tmp_path).valid is True


def test_validation_sanitizes_missing_extension_root_path(tmp_path):
    missing = tmp_path / "private-host-root" / "missing-extension"

    report = validate_extension_directory(missing)

    assert report.valid is False
    assert report.errors[0].code == "EXTENSION_ROOT_INVALID"
    assert report.errors[0].message == "extension root is invalid"
    assert str(tmp_path.resolve()) not in report.errors[0].message


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
