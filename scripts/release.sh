#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")"/.. && pwd)"
DIST_DIR="$PROJECT_ROOT/dist"
VENV_DIR="$PROJECT_ROOT/.venv-release"

function usage() {
  cat <<EOF
Usage: $(basename "$0") [--test]

Build wheel and sdist for the current project and upload via twine.

  --test    Upload to TestPyPI instead of PyPI.

Environment variables:
  PYPI_USERNAME / PYPI_PASSWORD or TWINE_USERNAME / TWINE_PASSWORD can be set.

EOF
}

TARGET_REPO="pypi"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --test)
      TARGET_REPO="testpypi"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

echo "[1/5] Preparing isolated build environment..."
if [[ ! -d "$VENV_DIR" ]]; then
  python -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --upgrade pip >/dev/null
"$VENV_DIR/bin/pip" install --upgrade build twine >/dev/null

echo "[2/5] Cleaning previous builds..."
rm -rf "$DIST_DIR"

echo "[3/5] Building distributions..."
"$VENV_DIR/bin/python" -m build "$PROJECT_ROOT"

echo "[4/5] Uploading to ${TARGET_REPO}..."
if [[ "$TARGET_REPO" == "testpypi" ]]; then
  "$VENV_DIR/bin/python" -m twine upload --repository testpypi "$DIST_DIR"/*
else
  "$VENV_DIR/bin/python" -m twine upload "$DIST_DIR"/*
fi

echo "[5/5] Release complete."
