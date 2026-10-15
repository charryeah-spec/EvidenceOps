from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd

from evidenceops.config import PROJECT_ROOT


def create_ablation_comparison(output_dir: Path) -> tuple[Path, Path]:
    rows = []
    for variant in ["raw", "evidence", "full"]:
        path = output_dir / f"metrics-{variant}.json"
        metrics = json.loads(path.read_text(encoding="utf-8"))
        rows.append(
            {
                "variant": variant,
                "detection_accuracy": metrics["course_detection_accuracy"],
                "localization_accuracy": metrics["localization_accuracy"],
                "root_cause_accuracy": metrics["root_cause_accuracy"],
                "avg_response_time_seconds": metrics[
                    "avg_response_time_all_seconds"
                ],
                "avg_tool_calls": metrics["avg_tool_calls"],
            }
        )

    frame = pd.DataFrame(rows)
    csv_path = output_dir / "ablation-summary.csv"
    frame.to_csv(csv_path, index=False)

    figure_dir = output_dir / "figures"
    figure_dir.mkdir(parents=True, exist_ok=True)
    figure_path = figure_dir / "ablation-comparison.png"
    figure, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    frame.set_index("variant")[
        ["detection_accuracy", "localization_accuracy", "root_cause_accuracy"]
    ].plot(kind="bar", ylim=(0, 1.05), ax=axes[0], color=["#2563EB", "#0D9488", "#F59E0B"])
    axes[0].set_title("Accuracy by ablation variant")
    axes[0].set_xlabel("")
    axes[0].set_ylabel("Score")
    axes[0].tick_params(axis="x", rotation=0)
    axes[0].legend(loc="lower right", fontsize=8)

    frame.plot(
        x="variant",
        y="avg_response_time_seconds",
        kind="bar",
        ax=axes[1],
        legend=False,
        color="#7C3AED",
    )
    axes[1].set_title("Average response time")
    axes[1].set_xlabel("")
    axes[1].set_ylabel("Seconds")
    axes[1].tick_params(axis="x", rotation=0)
    figure.tight_layout()
    figure.savefig(figure_path, dpi=180)
    plt.close(figure)
    return csv_path, figure_path


if __name__ == "__main__":
    csv, figure = create_ablation_comparison(PROJECT_ROOT / "results" / "ablation")
    print(f"Saved {csv} and {figure}")
