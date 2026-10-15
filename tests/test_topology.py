from evidenceops.models import EvidenceItem
from evidenceops.topology import ServiceTopology


def evidence(identifier, service, score, *, kind="metric", summary="anomaly"):
    return EvidenceItem(
        evidence_id=identifier,
        kind=kind,
        source=f"service/{service}",
        summary=summary,
        service=service,
        anomaly_score=score,
    )


def test_topology_ranks_deep_dependency_above_propagated_caller_symptoms():
    topology = ServiceTopology(
        ["frontend", "search", "geo"],
        [("frontend", "search"), ("search", "geo")],
    )

    result = topology.analyze(
        [
            evidence("frontend-errors", "frontend", 0.7),
            evidence("search-errors", "search", 0.8),
            evidence(
                "geo-image",
                "geo",
                0.9,
                kind="k8s_state",
                summary="known faulty image",
            ),
        ]
    )

    assert result.selected_service == "geo"
    assert result.confidence > 0.5
    assert result.scores[0].propagated_score > 0


def test_topology_ignores_runbooks_as_observations():
    topology = ServiceTopology(["user"], [])
    result = topology.analyze(
        [
            EvidenceItem(
                evidence_id="runbook",
                kind="runbook",
                source="runbook/pod",
                summary="pod failure",
                service="user",
                anomaly_score=1.0,
            )
        ]
    )

    assert result.selected_service is None


def test_topology_confidence_requires_absolute_evidence_strength():
    topology = ServiceTopology(["geo"], [])

    result = topology.analyze([evidence("weak", "geo", 0.1)])

    assert result.selected_service == "geo"
    assert result.confidence < 0.5
