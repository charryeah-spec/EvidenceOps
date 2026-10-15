from __future__ import annotations

import re
from pathlib import Path

from rank_bm25 import BM25Okapi

from evidenceops.models import EvidenceItem


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_\-]+|[\u4e00-\u9fff]", text.lower())


class RunbookRetriever:
    def __init__(self, directory: Path) -> None:
        self.paths = sorted(directory.glob("*.md"))
        self.documents = [path.read_text(encoding="utf-8") for path in self.paths]
        self.index = BM25Okapi([_tokens(doc) for doc in self.documents]) if self.documents else None

    def search(self, query: str, limit: int = 1) -> list[EvidenceItem]:
        if not self.index:
            return []
        scores = self.index.get_scores(_tokens(query))
        query_lower = query.lower()
        scores = [
            float(score) + _semantic_boost(path.stem, query_lower)
            for score, path in zip(scores, self.paths, strict=True)
        ]
        ranked = sorted(range(len(scores)), key=lambda index: scores[index], reverse=True)[:limit]
        results: list[EvidenceItem] = []
        for rank, index in enumerate(ranked, 1):
            if scores[index] <= 0:
                continue
            text = self.documents[index]
            summary = " ".join(line.strip() for line in text.splitlines() if line.strip())[:700]
            results.append(
                EvidenceItem(
                    evidence_id=f"runbook-{rank}-{self.paths[index].stem}",
                    kind="runbook",
                    source=str(self.paths[index]),
                    summary=summary,
                    value=round(float(scores[index]), 3),
                    anomaly_score=0.0,
                )
            )
        return results


def _semantic_boost(runbook_stem: str, query: str) -> float:
    """Boost unambiguous evidence phrases without using scenario identifiers."""
    indicators = {
        "app-misconfiguration": (
            "image changed",
            "faulty image",
            "imagepull",
            "geo:app3",
        ),
        "pod-failure": (
            "crashloop",
            "backoff",
            "restart count",
            "terminated unexpectedly",
        ),
        "network-loss": (
            "packet loss",
            "丢包",
        ),
        "network-delay": (
            "p95 latency",
            "p99 latency",
            "latency increased",
            "延迟",
        ),
    }
    return 8.0 if any(term in query for term in indicators.get(runbook_stem, ())) else 0.0
