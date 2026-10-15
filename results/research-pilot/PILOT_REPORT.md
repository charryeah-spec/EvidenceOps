# Qwen 2.5 14B research pilot

Date: 2026-10-14  
Machine architecture: Apple Silicon (`arm64`)  
Runtime: local Ollama OpenAI-compatible endpoint  
Model: `qwen2.5:14b`, 14.8B parameters, Q4_K_M, 32,768-token context  
Model digest: `7cdf5a0187d5c58cc5d369b255592f7841d1c4696d45a8c8a9489440385b22f6`

## Scope

This pilot compares the evidence-only rule baseline with local Qwen 2.5 14B on five unique
deterministic Fixture scenarios: image misconfiguration, pod failure, network packet loss, network
delay, and one healthy control. Temperature was zero and both systems used the same evidence,
topology, runbook, fault taxonomy, and metric contract.

This is an integration pilot, not a real-cluster benchmark. Five templates are too few for a
generalization or calibration claim.

## Failure-driven iteration

The first pass exposed a concrete model failure. On the pod-failure case, Qwen selected
`application_image_misconfiguration` with confidence 0.99 because the prompt contained the correct
pod-failure runbook and a lower-ranked image-misconfiguration runbook. It then proposed restoring a
geo image on the `user` target. The deterministic safety policy would have rejected that action due
to root-cause/action/target inconsistency.

The implementation was changed to:

1. retrieve exactly one runbook;
2. apply transparent evidence-phrase boosts for unambiguous terms such as `BackOff`, `packet loss`,
   `image changed`, and `P95 latency`, without reading the scenario ID;
3. skip the generative model entirely when the deterministic detection gate marks a window healthy;
4. record per-run token deltas instead of repeatedly summing the engine's cumulative counter.

Two regression tests were added for distractor exclusion and per-run token accounting.

## Final pilot result

| Model | Detection recall | Localization | Root cause | Coverage | ECE | Brier | Mean time | Tokens | Errors |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Evidence-only rule | 100% | 100% | 100% | 100% | 0.070 | 0.00570 | 0.00018 s | 0 | 0 |
| Qwen 2.5 14B | 100% | 100% | 100% | 100% | 0.018 | 0.00058 | 20.456 s | 4,566 | 0 |

Qwen produced the expected decision on all five final pilot cases. The healthy control was resolved
at the deterministic detection gate in 0.0003 seconds and consumed no model tokens. The four fault
diagnoses took 22.70-27.03 seconds each.

The calibration command returned temperature 1.0 because every final pilot outcome was correct;
there is no mixed correct/incorrect calibration sample from which to estimate a meaningful
temperature. The ECE and Brier values above are descriptive on this same tiny pilot only, not
held-out calibration performance.

## Artifacts

- `final-comparison/comparison.json`: machine-readable aggregate results.
- `final-comparison/comparison.csv`: model-level table.
- `final-comparison/comparison.md`: human-readable table.
- `final-comparison/*/records-full.csv`: per-case predictions and confidence.
- `final-comparison/*/figures/`: confusion, latency, and reliability plots.
- `pilot-calibration.json`: pilot-only temperature-scaling output and leakage warning.

## Next experiment

The next credible study should run repeated AIOpsLab injections rather than duplicate fixtures. Use
grouped calibration and held-out test partitions, multiple fault severities and workload levels,
unrelated distractors, and at least one additional model. The original five archived real-cluster
runs predate the topology and calibration extension and should not be merged with this pilot.
