from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path

import yaml

from evidenceops.models import (
    EvidenceItem,
    TopologyAnalysis,
    TopologyServiceScore,
)


class ServiceTopology:
    """Rank likely root services using dependency-aware anomaly propagation.

    Edges are stored as caller -> callee. A failing callee may create symptoms
    in its transitive callers, so caller anomalies provide discounted support
    for a candidate callee root cause.
    """

    def __init__(
        self,
        services: list[str],
        dependencies: list[tuple[str, str]],
        *,
        propagation_decay: float = 0.65,
        max_hops: int = 3,
    ) -> None:
        self.services = tuple(dict.fromkeys(services))
        self.dependencies = tuple(dependencies)
        self.propagation_decay = propagation_decay
        self.max_hops = max_hops
        self.callers_by_callee: dict[str, set[str]] = defaultdict(set)
        for caller, callee in self.dependencies:
            self.callers_by_callee[callee].add(caller)

    @classmethod
    def from_yaml(cls, path: Path) -> ServiceTopology:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        dependencies = [
            (str(item["caller"]), str(item["callee"]))
            for item in data.get("dependencies", [])
        ]
        services = [str(item) for item in data.get("services", [])]
        for caller, callee in dependencies:
            services.extend((caller, callee))
        return cls(
            services,
            dependencies,
            propagation_decay=float(data.get("propagation_decay", 0.65)),
            max_hops=int(data.get("max_hops", 3)),
        )

    @classmethod
    def empty(cls) -> ServiceTopology:
        return cls([], [])

    def analyze(self, evidence: list[EvidenceItem]) -> TopologyAnalysis:
        direct_scores: dict[str, float] = defaultdict(float)
        evidence_ids: dict[str, list[str]] = defaultdict(list)
        observed_services: set[str] = set()

        for item in evidence:
            if not item.service or item.kind == "runbook":
                continue
            service = item.service
            observed_services.add(service)
            weight = self._evidence_weight(item)
            score = item.anomaly_score * weight
            direct_scores[service] = max(direct_scores[service], score)
            if item.anomaly_score >= 0.5:
                evidence_ids[service].append(item.evidence_id)

        candidates = set(self.services) | observed_services
        scores: list[TopologyServiceScore] = []
        for candidate in candidates:
            propagated = 0.0
            for caller, distance in self._transitive_callers(candidate):
                propagated += (
                    direct_scores.get(caller, 0.0)
                    * (self.propagation_decay**distance)
                    * 0.45
                )
            direct = direct_scores.get(candidate, 0.0)
            scores.append(
                TopologyServiceScore(
                    service=candidate,
                    direct_score=round(direct, 6),
                    propagated_score=round(propagated, 6),
                    total_score=round(direct + propagated, 6),
                    supporting_evidence_ids=evidence_ids.get(candidate, [])[:8],
                )
            )

        scores.sort(key=lambda item: (-item.total_score, item.service))
        positive = [item for item in scores if item.total_score > 0]
        selected = positive[0] if positive else None
        second = positive[1].total_score if len(positive) > 1 else 0.0
        confidence = 0.0
        if selected:
            relative_margin = selected.total_score / (
                selected.total_score + second + 1e-9
            )
            absolute_strength = min(1.0, selected.total_score)
            confidence = relative_margin * absolute_strength
        explanation = "No service-scoped anomalous evidence was available."
        if selected:
            explanation = (
                f"Selected {selected.service}: direct={selected.direct_score:.3f}, "
                f"upstream-impact={selected.propagated_score:.3f}; "
                "confidence combines absolute evidence strength and the margin over "
                "the next candidate; caller symptoms are discounted by dependency distance."
            )
        return TopologyAnalysis(
            selected_service=selected.service if selected else None,
            confidence=max(0.0, min(1.0, confidence)),
            graph_node_count=len(candidates),
            graph_edge_count=len(self.dependencies),
            scores=scores[:10],
            explanation=explanation,
        )

    def _transitive_callers(self, service: str) -> list[tuple[str, int]]:
        queue = deque((caller, 1) for caller in self.callers_by_callee.get(service, set()))
        seen: dict[str, int] = {}
        while queue:
            caller, distance = queue.popleft()
            if distance > self.max_hops:
                continue
            previous = seen.get(caller)
            if previous is not None and previous <= distance:
                continue
            seen[caller] = distance
            for parent in self.callers_by_callee.get(caller, set()):
                queue.append((parent, distance + 1))
        return sorted(seen.items(), key=lambda item: (item[1], item[0]))

    @staticmethod
    def _evidence_weight(item: EvidenceItem) -> float:
        text = f"{item.source} {item.summary}".lower()
        if item.source.startswith("chaos/"):
            return 2.0
        if item.kind == "k8s_state" and any(
            marker in text for marker in ("faulty", "image changed", "imagepull")
        ):
            return 1.8
        if item.kind == "k8s_event":
            return 1.35
        if item.kind == "k8s_state":
            return 1.15
        if item.kind == "log":
            return 1.0
        return 0.9
