from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

import pandas as pd

from evidenceops.agent import EvidenceOpsAgent
from evidenceops.evaluation import run_suite
from evidenceops.models import GroundTruth


def run_model_comparison(
    agent_factory: Callable[[str], EvidenceOpsAgent],
    model_specs: list[str],
    cases: list[GroundTruth],
    output_dir: Path,
    *,
    variant: str = "full",
) -> dict:
    """Evaluate model specifications under one suite and metric contract."""
    if not model_specs:
        raise ValueError("At least one model specification is required")
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for spec in model_specs:
        model_dir = output_dir / _slug(spec)
        agent = agent_factory(spec)
        _, metrics = run_suite(
            agent,
            cases,
            model_dir,
            variant=variant,
            repair=False,
        )
        summaries.append(
            {
                "model_spec": spec,
                "model_name": ",".join(metrics["model_names"]),
                "case_count": metrics["case_count"],
                "unique_problem_count": metrics["unique_problem_count"],
                "fault_detection_recall": metrics["fault_detection_recall"],
                "localization_accuracy": metrics["localization_accuracy"],
                "root_cause_accuracy": metrics["root_cause_accuracy"],
                "coverage": metrics["coverage"],
                "selective_accuracy": metrics["selective_accuracy"],
                "abstention_rate": metrics["abstention_rate"],
                "ece": metrics["calibration"]["ece"],
                "brier_score": metrics["calibration"]["brier_score"],
                "avg_response_time_seconds": metrics["avg_response_time_all_seconds"],
                "total_token_usage": metrics["total_token_usage"],
                "error_count": metrics["error_count"],
            }
        )
    result = {
        "comparison_contract": {
            "suite_case_count": len(cases),
            "variant": variant,
            "temperature": 0,
            "note": "All models use the same evidence and metrics; calibration needs a held-out split for claims.",
        },
        "models": summaries,
    }
    (output_dir / "comparison.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    pd.DataFrame(summaries).to_csv(output_dir / "comparison.csv", index=False)
    lines = [
        "# EvidenceOps model comparison",
        "",
        "| Model | Detection recall | Localization | Root cause | Coverage | ECE | Mean time (s) | Tokens |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in summaries:
        lines.append(
            f"| {item['model_spec']} | {item['fault_detection_recall']:.1%} | "
            f"{item['localization_accuracy']:.1%} | {item['root_cause_accuracy']:.1%} | "
            f"{item['coverage']:.1%} | {item['ece']:.3f} | "
            f"{item['avg_response_time_seconds']:.3f} | {item['total_token_usage']} |"
        )
    (output_dir / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "model"
