from __future__ import annotations

import json
import shutil
from pathlib import Path

from evidenceops.config import PROJECT_ROOT
from evidenceops.models import IncidentReport

EXPECTED = {
    "misconfig_app_hotel_res-detection-1": {
        "detected": True,
        "service": "geo",
        "root_cause": "application_image_misconfiguration",
        "repair": True,
    },
    "pod_failure_hotel_res-detection-1": {
        "detected": True,
        "service": "user",
        "root_cause": "pod_failure",
        "repair": True,
    },
    "network_loss_hotel_res-detection-1": {
        "detected": True,
        "service": "user",
        "root_cause": "network_packet_loss",
        "repair": False,
    },
    "network_delay_hotel_res-detection-1": {
        "detected": True,
        "service": "user",
        "root_cause": "network_delay",
        "repair": False,
    },
    "noop_detection_hotel_reservation-1": {
        "detected": False,
        "service": None,
        "root_cause": None,
        "repair": False,
    },
}


def _is_valid(report: IncidentReport, expected: dict) -> bool:
    if (
        report.provider != "AIOpsLabProvider"
        or report.diagnosis_engine != "OpenAICompatibleDiagnosisEngine"
        or not report.model_name
        or report.error
    ):
        return False
    if report.detected != expected["detected"]:
        return False
    if report.faulty_service != expected["service"]:
        return False
    if report.root_cause != expected["root_cause"]:
        return False
    if expected["repair"]:
        return bool(
            report.approval_status == "approved"
            and report.verification
            and report.verification.healthy
            and any(
                action.success and not action.dry_run
                for action in report.executed_actions
            )
        )
    return not report.executed_actions


def build_real_acceptance_summary() -> dict:
    candidates: dict[str, list[tuple[Path, IncidentReport]]] = {
        problem_id: [] for problem_id in EXPECTED
    }
    for directory in (
        PROJECT_ROOT / "results" / "real",
        PROJECT_ROOT / "results" / "real-repair",
    ):
        for path in directory.rglob("*.json"):
            if "validated" in path.parts or path.name == "acceptance-summary.json":
                continue
            try:
                report = IncidentReport.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except Exception:
                continue
            if report.problem_id in candidates:
                candidates[report.problem_id].append((path, report))

    selected: dict[str, tuple[Path, IncidentReport]] = {}
    missing: list[str] = []
    for problem_id, expected in EXPECTED.items():
        valid = [
            item for item in candidates[problem_id] if _is_valid(item[1], expected)
        ]
        if not valid:
            missing.append(problem_id)
            continue
        selected[problem_id] = max(valid, key=lambda item: item[1].finished_at)

    output_dir = PROJECT_ROOT / "results" / "real" / "validated"
    output_dir.mkdir(parents=True, exist_ok=True)
    # Rebuild the validated export so older runs are not mixed into the five selected cases.
    for old_report in output_dir.iterdir():
        if old_report.suffix in {".json", ".md"}:
            old_report.unlink()
    cases = []
    for problem_id, (json_path, report) in selected.items():
        target_json = output_dir / json_path.name
        shutil.copy2(json_path, target_json)
        markdown_path = json_path.with_suffix(".md")
        if markdown_path.exists():
            shutil.copy2(markdown_path, output_dir / markdown_path.name)
        cases.append(
            {
                "problem_id": problem_id,
                "run_id": report.run_id,
                "detected": report.detected,
                "faulty_service": report.faulty_service,
                "root_cause": report.root_cause,
                "diagnosis_engine": report.diagnosis_engine,
                "model_name": report.model_name,
                "response_time_seconds": report.response_time_seconds,
                "verification_healthy": (
                    report.verification.healthy if report.verification else None
                ),
                "report": str(target_json.relative_to(PROJECT_ROOT)),
            }
        )

    fault_reports = [
        report
        for problem_id, (_, report) in selected.items()
        if EXPECTED[problem_id]["detected"]
    ]
    normal_report = selected.get("noop_detection_hotel_reservation-1")
    repairs = [
        report
        for problem_id, (_, report) in selected.items()
        if EXPECTED[problem_id]["repair"]
    ]
    summary = {
        "status": "passed" if not missing else "incomplete",
        "validated_at": "2026-08-29",
        "case_count": len(selected),
        "fault_case_count": len(fault_reports),
        "fault_detection_recall": (
            sum(report.detected for report in fault_reports) / len(fault_reports)
            if fault_reports
            else 0.0
        ),
        "normal_false_positive": (
            normal_report[1].detected if normal_report else None
        ),
        "localization_accuracy": (
            sum(
                report.faulty_service == EXPECTED[report.problem_id]["service"]
                for report in fault_reports
            )
            / len(fault_reports)
            if fault_reports
            else 0.0
        ),
        "root_cause_accuracy": (
            sum(
                report.root_cause == EXPECTED[report.problem_id]["root_cause"]
                for report in fault_reports
            )
            / len(fault_reports)
            if fault_reports
            else 0.0
        ),
        "repair_success_rate": (
            sum(bool(report.verification and report.verification.healthy) for report in repairs)
            / len(repairs)
            if repairs
            else None
        ),
        "avg_response_time_seconds": (
            sum(report.response_time_seconds for _, report in selected.values())
            / len(selected)
            if selected
            else 0.0
        ),
        "missing_problem_ids": missing,
        "cases": sorted(cases, key=lambda item: item["problem_id"]),
    }
    (output_dir / "acceptance-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# EvidenceOps real-environment validation summary",
        "",
        f"- Status: {summary['status']}",
        f"- Real scenarios: {summary['case_count']}/5",
        f"- Fault detection recall: {summary['fault_detection_recall']:.1%}",
        f"- Service localization accuracy: {summary['localization_accuracy']:.1%}",
        f"- Root-cause accuracy: {summary['root_cause_accuracy']:.1%}",
        (
            f"- Approved remediation success: {summary['repair_success_rate']:.1%}"
            if summary["repair_success_rate"] is not None
            else "- Approved remediation success: N/A"
        ),
        f"- Healthy-control false positive: {summary['normal_false_positive']}",
        "",
    ]
    for case in summary["cases"]:
        lines.append(
            f"- `{case['problem_id']}` → `{case['run_id']}`; "
            f"detected={case['detected']}; service={case['faulty_service']}; "
            f"root_cause={case['root_cause']}; model={case['model_name']}"
        )
    (output_dir / "acceptance-summary.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    return summary


if __name__ == "__main__":
    result = build_real_acceptance_summary()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "passed" else 1)
