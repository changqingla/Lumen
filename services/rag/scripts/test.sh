#!/usr/bin/env bash
set -euo pipefail

rag_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_root="$(cd "${rag_root}/../.." && pwd)"
export PYTHONPATH="${repo_root}/shared/python:${rag_root}:${rag_root}/api${PYTHONPATH:+:${PYTHONPATH}}"

python3 -m pytest -c "${rag_root}/pytest.ini" "${rag_root}/tests" "$@"
