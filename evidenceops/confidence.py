from __future__ import annotations

import math
from collections.abc import Iterable

from evidenceops.models import EvaluationRecord


def temperature_scale(confidence: float, temperature: float) -> float:
    """Scale a binary confidence through log-odds temperature scaling."""
    clipped = min(1 - 1e-6, max(1e-6, confidence))
    logit = math.log(clipped / (1 - clipped))
    return 1 / (1 + math.exp(-logit / temperature))


def diagnosis_correct(record: EvaluationRecord) -> bool:
    truth = record.ground_truth
    report = record.report
    if report.detected != truth.expected_detected:
        return False
    if not truth.expected_detected:
        return True
    return (
        not report.abstained
        and report.faulty_service == truth.expected_service
        and report.root_cause == truth.expected_root_cause
    )


def fit_temperature(
    confidences: Iterable[float],
    outcomes: Iterable[bool],
) -> float:
    """Fit one temperature by deterministic grid search on binary NLL."""
    pairs = list(zip(confidences, outcomes, strict=True))
    if len(pairs) < 2 or len({outcome for _, outcome in pairs}) < 2:
        return 1.0
    candidates = [0.25 + index * 0.05 for index in range(96)]

    def loss(temperature: float) -> float:
        total = 0.0
        for confidence, outcome in pairs:
            probability = temperature_scale(confidence, temperature)
            total -= math.log(probability if outcome else 1 - probability)
        return total / len(pairs)

    return min(candidates, key=loss)


def calibration_metrics(
    records: list[EvaluationRecord],
    *,
    bins: int = 10,
) -> dict:
    if not records:
        return {
            "sample_count": 0,
            "ece": 0.0,
            "brier_score": 0.0,
            "bins": [],
        }
    configured = [record.report.confidence for record in records]
    raw = [
        record.report.raw_confidence
        if record.report.raw_confidence is not None
        else record.report.confidence
        for record in records
    ]
    outcomes = [diagnosis_correct(record) for record in records]
    observed = calibration_from_arrays(configured, outcomes, bins=bins)
    raw_metrics = calibration_from_arrays(raw, outcomes, bins=bins)
    candidate_temperature = fit_temperature(raw, outcomes)
    candidate = calibration_from_arrays(
        [temperature_scale(value, candidate_temperature) for value in raw],
        outcomes,
        bins=bins,
    )
    return {
        **observed,
        "configured_temperatures": sorted(
            {record.report.confidence_temperature for record in records}
        ),
        "raw_ece": raw_metrics["ece"],
        "raw_brier_score": raw_metrics["brier_score"],
        "fitted_temperature_candidate": candidate_temperature,
        "fitted_candidate_ece": candidate["ece"],
        "fitted_candidate_brier_score": candidate["brier_score"],
        "fit_note": (
            "Reported ECE/Brier use the configured temperature. The fitted candidate "
            "uses this dataset only and must be evaluated on a held-out test split."
        ),
    }


def calibration_from_arrays(
    confidences: Iterable[float],
    outcomes: Iterable[bool],
    *,
    bins: int = 10,
) -> dict:
    """Compute ECE and Brier score without fitting on the evaluation data."""
    pairs = list(zip(confidences, outcomes, strict=True))
    if not pairs:
        return {
            "sample_count": 0,
            "ece": 0.0,
            "brier_score": 0.0,
            "bins": [],
        }
    normalized = [
        (min(1.0, max(0.0, confidence)), outcome)
        for confidence, outcome in pairs
    ]
    result_bins = []
    ece = 0.0
    for index in range(bins):
        lower = index / bins
        upper = (index + 1) / bins
        members = [
            (confidence, outcome)
            for confidence, outcome in normalized
            if lower <= confidence < upper or (index == bins - 1 and confidence == 1.0)
        ]
        if not members:
            continue
        mean_confidence = sum(value for value, _ in members) / len(members)
        accuracy = sum(outcome for _, outcome in members) / len(members)
        ece += abs(accuracy - mean_confidence) * len(members) / len(normalized)
        result_bins.append(
            {
                "lower": lower,
                "upper": upper,
                "count": len(members),
                "mean_confidence": mean_confidence,
                "accuracy": accuracy,
            }
        )
    brier = sum(
        (confidence - float(outcome)) ** 2
        for confidence, outcome in normalized
    ) / len(normalized)
    return {
        "sample_count": len(normalized),
        "ece": ece,
        "brier_score": brier,
        "bins": result_bins,
    }
