from __future__ import annotations

from collections import defaultdict

from evidenceops.models import EvidenceItem, ObservationBundle


class SignalDetector:
    def __init__(self, anomaly_threshold: float = 0.7) -> None:
        self.anomaly_threshold = anomaly_threshold

    def detect(self, observations: ObservationBundle) -> tuple[bool, float]:
        if not observations.evidence:
            return False, 0.0
        scores = sorted((item.anomaly_score for item in observations.evidence), reverse=True)
        high = [score for score in scores if score >= self.anomaly_threshold]
        detected = bool(high)
        confidence = min(1.0, (sum(high[:3]) / max(1, min(3, len(high))))) if high else 1.0 - max(scores)
        return detected, round(confidence, 3)

    def localize(self, evidence: list[EvidenceItem]) -> str | None:
        direct_chaos = [
            item
            for item in evidence
            if item.service
            and item.anomaly_score >= self.anomaly_threshold
            and item.source.startswith("chaos/")
        ]
        if direct_chaos:
            return max(direct_chaos, key=lambda item: item.anomaly_score).service

        # A deployment image marked anomalous is a direct configuration signal.
        # It must not be outvoted by several downstream pods that temporarily
        # become unready while the bad image propagates through dependencies.
        anomalous_images = [
            item
            for item in evidence
            if item.service
            and item.kind == "k8s_state"
            and item.evidence_id.startswith("image-")
            and item.anomaly_score >= 0.95
        ]
        if anomalous_images:
            return max(anomalous_images, key=lambda item: item.anomaly_score).service

        totals: dict[str, float] = defaultdict(float)
        counts: dict[str, int] = defaultdict(int)
        for item in evidence:
            if item.service and item.anomaly_score >= self.anomaly_threshold:
                totals[item.service] += item.anomaly_score
                counts[item.service] += 1
        if not totals:
            return None
        return max(totals, key=lambda service: (totals[service], counts[service]))
