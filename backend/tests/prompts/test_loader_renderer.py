from __future__ import annotations

from hashlib import sha256

import pytest

from benchmark.contracts import PromptRenderError
from benchmark.prompts import (
    PROMPT_FILE_MAX_BYTES,
    PromptLoadError,
    load_prompt_directory,
    validate_prompt_directory,
)


def _write_index(root, *, file="system.txt", variables="[portfolio]", optional=""):
    optional_block = f"\n    optional_variables:\n{optional}" if optional else ""
    (root / "index.yaml").write_text(
        "\n".join(
            [
                "api_version: 1",
                "prompts:",
                "  - id: core.example.system",
                "    version: 1.0.0",
                f"    file: {file}",
                f"    required_variables: {variables}{optional_block}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_file_provider_normalizes_lf_and_renders_canonical_json(tmp_path):
    _write_index(tmp_path, variables="[portfolio]", optional='      memory_block: ""')
    (tmp_path / "system.txt").write_bytes(
        b"Portfolio: {portfolio}\r\nMemory: {memory_block}\r\n"
    )

    loaded = load_prompt_directory(tmp_path, tmp_path / "index.yaml")
    rendered = loaded.provider.render(
        "core.example.system",
        {"portfolio": {"z": 1, "a": True}},
    )

    assert rendered.content == 'Portfolio: {"a":true,"z":1}\nMemory: \n'
    assert (
        rendered.content_sha256 == sha256(rendered.content.encode("utf-8")).hexdigest()
    )


def test_render_rejects_missing_and_unknown_variables(tmp_path):
    _write_index(tmp_path)
    (tmp_path / "system.txt").write_text("{portfolio}", encoding="utf-8")
    provider = load_prompt_directory(tmp_path, tmp_path / "index.yaml").provider

    with pytest.raises(PromptRenderError) as missing:
        provider.render("core.example.system", {})
    assert missing.value.code == "PROMPT_VARIABLES_INVALID"

    with pytest.raises(PromptRenderError) as unknown:
        provider.render("core.example.system", {"portfolio": "ok", "extra": True})
    assert unknown.value.code == "PROMPT_VARIABLES_INVALID"


@pytest.mark.parametrize(
    ("content", "expected_code"),
    [
        ("{portfolio.value}", "PROMPT_TEMPLATE_INVALID"),
        ("{portfolio!r}", "PROMPT_TEMPLATE_INVALID"),
        ("{portfolio:>10}", "PROMPT_TEMPLATE_INVALID"),
        ("{undeclared}", "PROMPT_VARIABLE_DECLARATION_MISMATCH"),
    ],
)
def test_loader_rejects_unsafe_or_mismatched_templates(
    tmp_path, content, expected_code
):
    _write_index(tmp_path)
    (tmp_path / "system.txt").write_text(content, encoding="utf-8")

    with pytest.raises(PromptLoadError) as caught:
        load_prompt_directory(tmp_path, tmp_path / "index.yaml")

    codes = {item["code"] for item in caught.value.to_dict()["details"]["errors"]}
    assert expected_code in codes


def test_prompt_file_cannot_escape_root(tmp_path):
    root = tmp_path / "prompts"
    root.mkdir()
    (tmp_path / "outside.txt").write_text("{portfolio}", encoding="utf-8")
    _write_index(root, file="../outside.txt")

    report = validate_prompt_directory(root, root / "index.yaml")

    assert report.valid is False
    assert {issue.code for issue in report.errors} == {"PROMPT_FILE_PATH_INVALID"}


def test_duplicate_yaml_keys_are_rejected(tmp_path):
    (tmp_path / "index.yaml").write_text(
        "api_version: 1\napi_version: 1\nprompts: []\n",
        encoding="utf-8",
    )

    report = validate_prompt_directory(tmp_path, tmp_path / "index.yaml")

    assert report.valid is False
    assert report.errors[0].code == "PROMPT_INDEX_DUPLICATE_KEY"


def test_prompt_file_size_and_utf8_are_validated(tmp_path):
    _write_index(tmp_path, variables="[]")
    prompt_file = tmp_path / "system.txt"
    prompt_file.write_bytes(b"x" * (PROMPT_FILE_MAX_BYTES + 1))

    oversized = validate_prompt_directory(tmp_path, tmp_path / "index.yaml")

    assert oversized.valid is False
    assert oversized.errors[0].code == "PROMPT_FILE_TOO_LARGE"

    prompt_file.write_bytes(b"\xff")
    invalid_utf8 = validate_prompt_directory(tmp_path, tmp_path / "index.yaml")

    assert invalid_utf8.valid is False
    assert invalid_utf8.errors[0].code == "PROMPT_FILE_INVALID_UTF8"


def test_prompt_profiles_are_loaded_as_immutable_bindings(tmp_path):
    _write_index(tmp_path, variables="[]")
    index = tmp_path / "index.yaml"
    index.write_text(
        index.read_text(encoding="utf-8")
        + "profiles:\n"
        + "  - id: core.example.default\n"
        + "    version: 1.0.0\n"
        + "    slots:\n"
        + "      system:\n"
        + "        prompt_id: core.example.system\n",
        encoding="utf-8",
    )
    (tmp_path / "system.txt").write_text("Static", encoding="utf-8")

    loaded = load_prompt_directory(tmp_path, index)

    assert loaded.profiles[0].slots["system"].prompt_id == "core.example.system"
    with pytest.raises(TypeError):
        loaded.profiles[0].slots["system"] = loaded.profiles[0].slots["system"]
