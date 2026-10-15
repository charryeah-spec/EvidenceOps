# Results

This directory contains a small, curated validation snapshot.

- `real/validation-summary.json`: five real AIOpsLab + Qwen runs (four faults and one healthy control).
- `metrics-full.json`: deterministic engineering regression. It reports five unique templates, 19 repeated runs, coverage, selective accuracy, ECE, and Brier score.
- `records-full.csv`: per-run predictions, raw/calibrated confidence, abstention, and correctness.
- `figures/`: confusion matrix, response-time plot, and confidence reliability diagram.
- `research-pilot/`: local Qwen 2.5 14B failure analysis, final five-template comparison, per-case records, figures, and pilot-only calibration artifact.
- `real/research-upgrade/`: topology-aware real-cluster snapshot, hashed source manifest, aggregate metrics, portable per-case records, and figures.

The deterministic results validate program behavior and metric calculations. They are not evidence
of LLM generalization or held-out calibration quality. The new real-cluster snapshot exercises the
topology-aware pipeline but remains a five-case feasibility check with one run per condition.
