#!/usr/bin/env bash
set -euo pipefail

python_bin="${EVIDENCEOPS_PYTHON:-.venv/bin/python}"

"${python_bin}" -m evidenceops evaluate --suite configs/ablation-cases.yaml --provider fixture --engine rule --variant raw --output results/ablation
"${python_bin}" -m evidenceops evaluate --suite configs/ablation-cases.yaml --provider fixture --engine rule --variant evidence --output results/ablation
"${python_bin}" -m evidenceops evaluate --suite configs/ablation-cases.yaml --provider fixture --engine rule --variant full --output results/ablation
"${python_bin}" -m evidenceops.ablation
