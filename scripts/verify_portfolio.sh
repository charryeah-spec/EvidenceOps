#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

export PYTHONDONTWRITEBYTECODE=1

python -m ruff check --no-cache .
python -m pytest -q -p no:cacheprovider
python -m evidenceops evaluate \
  --suite configs/cases.yaml \
  --provider fixture \
  --engine rule \
  --variant full \
  --output /tmp/evidenceops-portfolio-verification

echo "Portfolio verification passed."
