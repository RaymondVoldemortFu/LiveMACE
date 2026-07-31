"""Command-line extension Manifest validator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from benchmark.contracts import ValidationReport, to_jsonable

from .validation import validate_extension_directory


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a benchmark extension directory"
    )
    parser.add_argument("directory", type=Path)
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def _print_text(report: ValidationReport) -> None:
    if report.valid:
        print("Extension is valid.")
        return
    print("Extension is invalid.")
    for issue in report.errors:
        location = issue.path or "manifest"
        code = f" [{issue.code}]" if issue.code else ""
        print(f"ERROR {location}{code}: {issue.message}")
    for issue in report.warnings:
        location = issue.path or "manifest"
        code = f" [{issue.code}]" if issue.code else ""
        print(f"WARNING {location}{code}: {issue.message}")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    report = validate_extension_directory(arguments.directory)
    if arguments.json_output:
        print(json.dumps(to_jsonable(report), sort_keys=True, ensure_ascii=False))
    else:
        _print_text(report)
    return 0 if report.valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
