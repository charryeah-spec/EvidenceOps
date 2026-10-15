#!/usr/bin/env bash
set -euo pipefail

failed=0
python_bin="${EVIDENCEOPS_PYTHON:-.venv/bin/python}"
if [[ -x "${python_bin}" ]]; then
  echo "[OK] python: $("${python_bin}" --version 2>&1) (${python_bin})"
else
  echo "[MISSING] Python 3.11 virtual environment at ${python_bin}"
  failed=1
fi

commands=(docker kind kubectl helm)
for command_name in "${commands[@]}"; do
  if command -v "${command_name}" >/dev/null 2>&1; then
    echo "[OK] ${command_name}: $(command -v "${command_name}")"
  else
    echo "[MISSING] ${command_name}"
    failed=1
  fi
done

if command -v poetry >/dev/null 2>&1; then
  echo "[OK] poetry (optional): $(command -v poetry)"
else
  echo "[OPTIONAL] poetry is not installed; EvidenceOps uses pip/pyproject.toml."
fi

docker info >/dev/null 2>&1 || {
  echo "[MISSING] Docker daemon is not reachable"
  failed=1
}

if [[ "${failed}" -ne 0 ]]; then
  exit 1
fi
echo "Environment prerequisites are ready."
