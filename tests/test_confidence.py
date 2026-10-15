from datetime import UTC, datetime

from evidenceops.confidence import calibration_metrics, temperature_scale
from evidenceops.models import EvaluationRecord, GroundTruth, IncidentReport


def record(case_id: str, confidence: float, *, correct: bool) -> EvaluationRecord:
    now = datetime.now(UTC)
    truth = GroundTruth(
        case_id=case_id,
        problem_id=case_id,
        expected_detected=True,
        expected_service="geo",
        expected_root_cause="pod_failure",
        fault_type="pod_failure",
    )
    report = IncidentReport(
        run_id=case_id,
        problem_id=case_id,
        started_at=now,
        finished_at=now,
        detected=True,
        faulty_service="geo",
        root_cause="pod_failure" if correct else "network_delay",
        confidence=confidence,
        response_time_seconds=0.1,
    )
    return EvaluationRecord(ground_truth=truth, report=report)


def test_temperature_scaling_moves_overconfident_probability_toward_half():
    assert temperature_scale(0.9, 2.0) < 0.9
    assert temperature_scale(0.9, 2.0) > 0.5


def test_calibration_reports_ece_brier_and_bins():
    metrics = calibration_metrics(
        [
            record("a", 0.95, correct=True),
            record("b", 0.9, correct=False),
            record("c", 0.7, correct=True),
            record("d", 0.6, correct=False),
        ]
    )

    assert metrics["sample_count"] == 4
    assert 0 <= metrics["ece"] <= 1
    assert 0 <= metrics["brier_score"] <= 1
    assert metrics["bins"]
