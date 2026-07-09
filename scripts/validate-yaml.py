#!/usr/bin/env python3
# ==============================================================================
# dessmonitor YAML Syntax Validation
# ==============================================================================
# Validates all YAML files in the repository with multi-document support.
# Exits 0 on success, 1 on any syntax error.
# Does not require Docker, K8s API, or network access.
# Does not read secret values.
# ==============================================================================

import os
import sys

import yaml


# Directories to exclude from YAML scanning
EXCLUDED_DIRS = {".git", ".project-memory", "ml_data", "venv", ".venv", "__pycache__"}


def find_yaml_files(root_dir: str) -> list[str]:
    """Recursively find all *.yaml and *.yml files, excluding known dirs."""
    yaml_files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root_dir):
        # Filter out excluded directories in-place so os.walk does not descend
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]

        for fname in filenames:
            if fname.endswith(".yaml") or fname.endswith(".yml"):
                yaml_files.append(os.path.join(dirpath, fname))
    return sorted(yaml_files)


def validate_file(filepath: str) -> list[str]:
    """Validate a single YAML file. Returns list of error strings."""
    errors: list[str] = []

    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            content = fh.read()
    except OSError as exc:
        rel = os.path.relpath(filepath)
        errors.append(f"ERROR: {rel}: cannot read file: {exc}")
        return errors

    # Skip empty or whitespace-only files
    if not content.strip():
        return errors

    rel = os.path.relpath(filepath)

    try:
        # safe_load_all handles multi-document YAML (--- separator)
        docs = list(yaml.safe_load_all(content))
        # Check that all documents parsed; empty list means valid empty file
        # If an empty string was passed, safe_load_all yields None — that's fine.
    except yaml.YAMLError as exc:
        # Extract line information if available
        if hasattr(exc, "problem_mark") and exc.problem_mark is not None:
            line = exc.problem_mark.line + 1  # 1-based line numbers
            problem = exc.problem or str(exc)
            errors.append(f"ERROR: {rel}: line {line}: {problem}")
        else:
            errors.append(f"ERROR: {rel}: {exc}")
    except Exception as exc:
        errors.append(f"ERROR: {rel}: unexpected error: {exc}")

    return errors


def main() -> int:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    yaml_files = find_yaml_files(repo_root)

    if not yaml_files:
        print("No YAML files found to validate.")
        return 0

    all_errors: list[str] = []
    for filepath in yaml_files:
        all_errors.extend(validate_file(filepath))

    ok_count = len(yaml_files) - len(
        set(
            os.path.relpath(
                os.path.dirname(e.split(":")[1].strip())
                if e.count(":") >= 2
                else ""
            )
            for e in all_errors
        )
    )

    for err in all_errors:
        print(err)

    if all_errors:
        print(f"\n❌ {len(all_errors)} error(s) in {len(yaml_files)} file(s) checked.")
        return 1

    print(f"\n✅ All {len(yaml_files)} YAML file(s) validated successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
