from __future__ import annotations

import json
from datetime import UTC
from math import sqrt
from pathlib import Path
from statistics import mean

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
import yaml
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)

from evidenceops.agent import EvidenceOpsAgent
from evidenceops.confidence import calibration_metrics, diagnosis_correct
from evidenceops.models import EvaluationRecord, GroundTruth
from evidenceops.reporting import write_report


def load_suite(path: Path) -> list[GroundTruth]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [GroundTruth.model_validate(item) for item in data["cases"]]


def run_suite(
    agent: EvidenceOpsAgent,
    cases: list[GroundTruth],
    output_dir: Path,
    *,
    variant: str = "full",
    repair: bool = False,
) -> tuple[list[EvaluationRecord], dict]:
    records: list[EvaluationRecord] = []
    run_label = f"{variant}-repair" if repair else variant
    raw_dir = output_dir / "raw" / run_label
    for case in cases:
        try:
            should_repair = repair and case.auto_remediable
            report = agent.run(
                case.problem_id,
                mode="repair" if should_repair else "diagnose",
                approved=should_repair,
                variant=variant,
            )
        except Exception as exc:  # Keep one failed case from stopping the batch.
            report = _failure_report(case, str(exc))
        write_report(report, raw_dir)
        records.append(EvaluationRecord(ground_truth=case, report=report, variant=run_label))
        agent.provider.cleanup(case.problem_id)
    metrics = calculate_metrics(records)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"metrics-{run_label}.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    write_records_csv(records, output_dir / f"records-{run_label}.csv")
    create_figures(records, output_dir / "figures", run_label)
    return records, metrics


def calculate_metrics(records: list[EvaluationRecord]) -> dict:
    y_true = [record.ground_truth.expected_detected for record in records]
    y_pred = [record.report.detected for record in records]
    faulty = [record for record in records if record.ground_truth.expected_detected]
    detection_hits = sum(record.report.detected for record in faulty)
    localization_hits = sum(
        record.report.faulty_service == record.ground_truth.expected_service for record in faulty
    )
    root_cause_hits = sum(
        not record.report.abstained
        and record.report.root_cause == record.ground_truth.expected_root_cause
        for record in faulty
    )
    normal = [record for record in records if not record.ground_truth.expected_detected]
    false_positives = sum(record.report.detected for record in normal)
    repair_attempts = [
        record
        for record in records
        if record.ground_truth.auto_remediable
        and record.report.approval_status == "approved"
    ]
    repair_successes = [
        record
        for record in repair_attempts
        if record.report.verification
        and record.report.verification.healthy
        and any(
            action.success and not action.dry_run
            for action in record.report.executed_actions
        )
    ]
    recovery_times = []
    executed = []
    for record in repair_attempts:
        actual_actions = [
            action for action in record.report.executed_actions if not action.dry_run
        ]
        executed.extend(actual_actions)
        if actual_actions and record.report.verification:
            recovery_times.append(
                max(
                    0.0,
                    (
                        record.report.finished_at - actual_actions[0].started_at
                    ).total_seconds(),
                )
            )
    detection_accuracy = detection_hits / len(faulty) if faulty else 0.0
    detection_ci_low, detection_ci_high = _wilson_interval(
        detection_hits,
        len(faulty),
    )
    unique_problem_ids = {record.ground_truth.problem_id for record in records}
    unique_fault_problem_ids = {
        record.ground_truth.problem_id for record in faulty
    }
    decided = [record for record in records if not record.report.abstained]
    selective_hits = sum(diagnosis_correct(record) for record in decided)
    return {
        "providers": sorted({record.report.provider for record in records}),
        "diagnosis_engines": sorted(
            {record.report.diagnosis_engine for record in records}
        ),
        "model_names": sorted({record.report.model_name for record in records}),
        "variants": sorted({record.variant for record in records}),
        "synthetic_fixture_results": bool(records)
        and all(record.report.provider == "FixtureProvider" for record in records),
        "case_count": len(records),
        "fault_case_count": len(faulty),
        "unique_problem_count": len(unique_problem_ids),
        "unique_fault_problem_count": len(unique_fault_problem_ids),
        "repeated_case_count": max(0, len(records) - len(unique_problem_ids)),
        "fault_detection_recall": detection_accuracy,
        "course_detection_accuracy": detection_accuracy,
        "detection_accuracy_95ci": {
            "low": detection_ci_low,
            "high": detection_ci_high,
            "method": "Wilson score",
        },
        "overall_accuracy": accuracy_score(y_true, y_pred) if records else 0.0,
        "precision": precision_score(y_true, y_pred, zero_division=0),
        "recall": recall_score(y_true, y_pred, zero_division=0),
        "f1": f1_score(y_true, y_pred, zero_division=0),
        "false_positive_rate": false_positives / len(normal) if normal else 0.0,
        "localization_accuracy": localization_hits / len(faulty) if faulty else 0.0,
        "root_cause_accuracy": root_cause_hits / len(faulty) if faulty else 0.0,
        "abstention_count": sum(record.report.abstained for record in records),
        "abstention_rate": (
            sum(record.report.abstained for record in records) / len(records)
            if records
            else 0.0
        ),
        "coverage": len(decided) / len(records) if records else 0.0,
        "selective_accuracy": selective_hits / len(decided) if decided else None,
        "calibration": calibration_metrics(records),
        "avg_response_time_all_seconds": mean(record.report.response_time_seconds for record in records) if records else 0.0,
        "avg_response_time_faults_seconds": mean(record.report.response_time_seconds for record in faulty) if faulty else 0.0,
        "avg_tool_calls": mean(record.report.tool_call_count for record in records) if records else 0.0,
        "total_token_usage": sum(record.report.token_usage for record in records),
        "repair_case_count": len(repair_attempts),
        "repair_success_rate": len(repair_successes) / len(repair_attempts) if repair_attempts else None,
        "avg_recovery_time_seconds": mean(recovery_times) if recovery_times else None,
        "invalid_operation_rate": sum(not action.success for action in executed) / len(executed) if executed else None,
        "error_count": sum(record.report.error is not None for record in records),
    }


def _wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Compute a binomial proportion interval without extra dependencies."""
    if total <= 0:
        return 0.0, 0.0
    proportion = successes / total
    denominator = 1 + (z**2 / total)
    centre = proportion + (z**2 / (2 * total))
    margin = z * sqrt(
        (proportion * (1 - proportion) / total) + (z**2 / (4 * total**2))
    )
    return (
        max(0.0, (centre - margin) / denominator),
        min(1.0, (centre + margin) / denominator),
    )


def create_figures(records: list[EvaluationRecord], output_dir: Path, variant: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    y_true = [int(record.ground_truth.expected_detected) for record in records]
    y_pred = [int(record.report.detected) for record in records]
    matrix = confusion_matrix(y_true, y_pred, labels=[0, 1])
    plt.figure(figsize=(5, 4))
    sns.heatmap(matrix, annot=True, fmt="d", cmap="Blues", xticklabels=["Normal", "Fault"], yticklabels=["Normal", "Fault"])
    plt.xlabel("Predicted")
    plt.ylabel("Actual")
    plt.tight_layout()
    plt.savefig(output_dir / f"confusion-matrix-{variant}.png", dpi=180)
    plt.close()

    frame = pd.DataFrame({"fault_type": [r.ground_truth.fault_type for r in records], "seconds": [r.report.response_time_seconds for r in records]})
    plt.figure(figsize=(8, 4))
    sns.boxplot(data=frame, x="fault_type", y="seconds")
    plt.xticks(rotation=25, ha="right")
    plt.ylabel("Response time (seconds)")
    plt.tight_layout()
    plt.savefig(output_dir / f"response-time-{variant}.png", dpi=180)
    plt.close()

    calibration = calibration_metrics(records)
    plt.figure(figsize=(5, 4))
    plt.plot([0, 1], [0, 1], linestyle="--", color="gray", label="Perfect calibration")
    if calibration["bins"]:
        plt.plot(
            [item["mean_confidence"] for item in calibration["bins"]],
            [item["accuracy"] for item in calibration["bins"]],
            marker="o",
            color="#1676b9",
            label=f"ECE={calibration['ece']:.3f}",
        )
    plt.xlabel("Mean calibrated confidence")
    plt.ylabel("Empirical accuracy")
    plt.xlim(0, 1)
    plt.ylim(0, 1)
    plt.legend(loc="lower right")
    plt.tight_layout()
    plt.savefig(output_dir / f"reliability-{variant}.png", dpi=180)
    plt.close()


def write_records_csv(records: list[EvaluationRecord], path: Path) -> None:
    """Write a portable, evidence-free table of evaluation decisions."""
    rows = []
    for record in records:
        topology = record.report.topology_analysis
        rows.append({
            "case_id": record.ground_truth.case_id,
            "problem_id": record.ground_truth.problem_id,
            "run_id": record.report.run_id,
            "fault_type": record.ground_truth.fault_type,
            "provider": record.report.provider,
            "diagnosis_engine": record.report.diagnosis_engine,
            "model_name": record.report.model_name,
            "variant": record.report.variant,
            "expected_detected": record.ground_truth.expected_detected,
            "predicted_detected": record.report.detected,
            "expected_service": record.ground_truth.expected_service,
            "predicted_service": record.report.faulty_service,
            "expected_root_cause": record.ground_truth.expected_root_cause,
            "predicted_root_cause": record.report.root_cause,
            "raw_confidence": record.report.raw_confidence,
            "calibrated_confidence": record.report.confidence,
            "confidence_temperature": record.report.confidence_temperature,
            "abstained": record.report.abstained,
            "diagnosis_correct": diagnosis_correct(record),
            "topology_service": topology.selected_service if topology else None,
            "topology_confidence": topology.confidence if topology else None,
            "response_time_seconds": record.report.response_time_seconds,
            "tool_call_count": record.report.tool_call_count,
            "token_usage": record.report.token_usage,
            "error": record.report.error,
        })
    pd.DataFrame(rows).to_csv(path, index=False)


def _failure_report(case: GroundTruth, error: str):
    from datetime import datetime

    from evidenceops.models import IncidentReport

    now = datetime.now(UTC)
    return IncidentReport(
        run_id=f"FAILED-{case.case_id}", problem_id=case.problem_id, started_at=now, finished_at=now,
        detected=False, response_time_seconds=0.0, error=error,
    )
