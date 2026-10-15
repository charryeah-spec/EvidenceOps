# Research evaluation protocol

This extension turns EvidenceOps from a course-completion demo into a small but explicit
research scaffold. It does not turn the five unique fixture templates into a model benchmark.

## Research questions

1. Does dependency-aware localization separate a failing callee from anomalous callers better
   than evidence-only ranking?
2. How do rule, local, and hosted models trade diagnosis accuracy, coverage, latency, and tokens
   under an identical evidence budget?
3. Does temperature scaling reduce confidence error on a held-out test set, and does abstention
   reduce unsafe or incorrect decisions at an acceptable coverage cost?

## Topology-aware diagnosis

`configs/service-topology.yaml` declares directed caller-to-callee edges. For each candidate root
service, the ranker combines direct service-scoped anomaly evidence with discounted symptoms from
its transitive callers. Kubernetes state and injected-fault selectors receive higher weights than
logs and metrics; runbooks are excluded because they are reference documents, not observations.

Every report records the graph size, ranked services, direct and propagated scores, supporting
evidence IDs, selected service, and ranking confidence. A useful ablation compares topology enabled
and disabled on incidents containing downstream distractor symptoms.

## Model comparison contract

All compared models must use the same suite, provider snapshot, prompt template, evidence budget,
temperature 0, timeout/retry policy, and fault taxonomy. Run:

```bash
python -m evidenceops compare \
  --models rule,qwen2.5:7b,qwen2.5:14b \
  --suite configs/cases.yaml --provider fixture \
  --output results/comparison
```

The command writes one full evaluation directory per model plus `comparison.json`,
`comparison.csv`, and `comparison.md`. Compare fault-detection recall, localization and root-cause
accuracy, coverage, selective accuracy, abstention rate, ECE, Brier score, latency, tokens, and
errors. Fixture output is an engineering regression result; publishable claims require repeated
real-cluster trials with varied severity, timing, load, and distractors.

## Confidence calibration without test leakage

Create disjoint calibration and test splits at the *incident template or experiment-run level*;
do not randomly split repeated copies of the same fixture. Run the calibration split, then fit:

```bash
python -m evidenceops fit-calibration \
  --records results/calibration/records-full.csv \
  --output results/confidence-calibration.json
```

Set the emitted `CONFIDENCE_TEMPERATURE` before running the untouched test split. The evaluator's
reported ECE and Brier score use the configured temperature; the fitted candidate embedded in an
evaluation file is diagnostic only and must not be presented as held-out performance.

At runtime, calibrated confidence below `MIN_DIAGNOSIS_CONFIDENCE` produces an explicit abstention.
Abstention removes automated action proposals. The safety boundary independently requires a typed
incident context, supporting observed evidence, sufficient confidence, and agreement between the
action target and topology-aware localization.

## Minimum credible study

- At least 30 independently injected real runs per major condition, not repeated serialization of
  identical fixtures.
- Multiple fault magnitudes and workload levels, plus healthy and unrelated-distractor windows.
- A pre-declared calibration split and untouched test split grouped by scenario family.
- Bootstrap or binomial confidence intervals and per-run artifacts.
- Error analysis for wrong localization, wrong root-cause class, abstention, and unsafe-action
  rejection.
- Exact model identifiers, endpoint/runtime versions, seeds where applicable, hardware, prompt
  budget, AIOpsLab commit, and execution date.

The 2026-10-14 five-run Qwen snapshot exercises the topology-aware pipeline on four real injected
faults and one healthy control. All expected decisions, services, and root causes were correct, but
one run per condition remains evidence of feasibility rather than comparative performance or
calibration quality. Its hashed manifest and summary are in `results/real/research-upgrade/`.
