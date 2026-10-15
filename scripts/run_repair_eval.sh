#!/usr/bin/env bash
set -euo pipefail

python_bin="${EVIDENCEOPS_PYTHON:-.venv/bin/python}"

"${python_bin}" -m evidenceops evaluate \
  --suite configs/repair-cases.yaml \
  --provider fixture \
  --engine rule \
  --variant full \
  --output results/repair \
  --repair
