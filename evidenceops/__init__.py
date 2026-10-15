"""EvidenceOps package."""

import os
from pathlib import Path

_cache_dir = Path(__file__).resolve().parents[1] / ".cache" / "matplotlib"
_cache_dir.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(_cache_dir))
_tiktoken_cache = Path(__file__).resolve().parents[1] / ".cache" / "tiktoken"
_tiktoken_cache.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(_tiktoken_cache))

from .agent import EvidenceOpsAgent  # noqa: E402
from .models import IncidentReport  # noqa: E402

__all__ = ["EvidenceOpsAgent", "IncidentReport"]
__version__ = "0.1.0"
