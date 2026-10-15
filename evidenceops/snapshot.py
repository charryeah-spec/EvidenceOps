from __future__ import annotations

import hashlib
import json
from pathlib import Path

from evidenceops.evaluation import (
    calculate_metrics,
    create_figures,
    write_records_csv,
)
from evidenceops.models import EvaluationRecord, GroundTruth, IncidentReport


def summarize_reports(
    report_dir: Path,
    cases: list[GroundTruth],
    output_dir: Path,
    *,
    label: str = "snapshot",
) -> tuple[list[EvaluationRecord], dict, dict]:
    """Create reproducible evaluation artifacts from previously saved reports.

    The newest valid report is selected independently for each problem in the
    supplied suite. A manifest records the exact files and SHA-256 hashes used.
    """
    wanted = {case.problem_id for case in cases}
    candidates: dict[str, list[tuple[IncidentReport, Path]]] = {
        problem_id: [] for problem_id in wanted
    }
    for path in sorted(report_dir.rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        problem_id = payload.get("problem_id") if isinstance(payload, dict) else None
        if problem_id not in wanted:
            continue
        report = IncidentReport.model_validate(payload)
        if report.error is None:
            candidates[problem_id].append((report, path))

    missing = sorted(problem_id for problem_id, items in candidates.items() if not items)
    if missing:
        raise ValueError(f"No successful report found for: {', '.join(missing)}")

    records: list[EvaluationRecord] = []
    selected: list[dict] = []
    for case in cases:
        report, path = max(
            candidates[case.problem_id],
            key=lambda item: (item[0].finished_at, item[0].run_id),
        )
        records.append(EvaluationRecord(ground_truth=case, report=report, variant=label))
        selected.append(
            {
                "case_id": case.case_id,
                "problem_id": case.problem_id,
                "run_id": report.run_id,
                "finished_at": report.finished_at.isoformat(),
                "source_file": path.relative_to(report_dir).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )

    metrics = calculate_metrics(records)
    manifest = {
        "label": label,
        "selection_policy": (
            "newest successful IncidentReport by finished_at for each suite problem_id"
        ),
        "report_root": report_dir.name,
        "case_count": len(cases),
        "selected_reports": selected,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"metrics-{label}.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / f"manifest-{label}.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_records_csv(records, output_dir / f"records-{label}.csv")
    create_figures(records, output_dir / "figures", label)
    return records, metrics, manifest
