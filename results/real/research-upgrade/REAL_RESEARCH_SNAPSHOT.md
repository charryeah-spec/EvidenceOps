# Real-cluster research snapshot

## Claim boundary

This snapshot evaluates the topology-aware EvidenceOps pipeline on five unique Microsoft AIOpsLab
problems: four injected faults and one healthy control. It is an end-to-end feasibility study, not a
generalization benchmark or a held-out calibration study.

## Reproducible setup

- Date: 2026-10-14 (Asia/Shanghai)
- Application: AIOpsLab Hotel Reservation on a local Kind cluster
- AIOpsLab commit: `80901cc77de13a8fb35dc0e3feff78ca09fd6ae4`
- Model: Ollama `qwen2.5:14b`, ID `7cdf5a0187d5`, 14.8B parameters, Q4_K_M
- Prompt budget: at most 18 selected evidence items; full evidence remains in each incident report
- Host: Apple Silicon macOS, 10 logical CPUs, 32 GiB RAM
- Runtime: Docker Desktop 29.6.2, Kind 0.32.0, kubectl 1.36.1
- Evaluation suite: `configs/research-pilot-cases.yaml`
- Selection rule: newest successful report per suite `problem_id`

The exact selected run IDs, relative source paths, and SHA-256 hashes are recorded in
`summary/manifest-qwen-real-topology.json`. The summary was generated from saved reports rather
than manually transcribed decisions:

```bash
python -m evidenceops summarize-reports \
  --reports results/real/research-upgrade \
  --suite configs/research-pilot-cases.yaml \
  --output results/real/research-upgrade/summary \
  --label qwen-real-topology
```

## Per-scenario results

| Scenario | Decision | Service / root cause | Topology confidence | Time | Tokens |
|---|---|---|---:|---:|---:|
| Bad application image | correct fault | `geo` / `application_image_misconfiguration` | 0.645 | 110.05 s | 3,882 |
| Pod failure | correct fault | `user` / `pod_failure` | 0.509 | 88.05 s | 3,516 |
| Network packet loss | correct fault | `user` / `network_packet_loss` | 0.724 | 121.23 s | 3,991 |
| Network delay | correct fault | `user` / `network_delay` | 0.614 | 71.88 s | 3,683 |
| Healthy control | correct no-alert | no service / no root cause | not needed | 62.22 s | 0 |

The healthy path did not invoke the language model. It retained 129 evidence items in the report,
but the deterministic detector correctly stopped the pipeline before diagnosis or action planning.

## Aggregate metrics

- Fault detection recall: 4/4 (100%); Wilson 95% CI: 51.0%–100.0%
- Overall accuracy: 5/5 (100%); false-positive rate: 0/1 (0%)
- Service localization: 4/4 (100%)
- Root-cause classification: 4/4 (100%)
- Coverage and selective accuracy: 100% and 100%; no abstentions
- Mean end-to-end time: 90.69 s across all cases; 97.80 s across fault cases
- Total model tokens: 15,072; mean tool calls: 1.8
- Configured-temperature ECE: 0.146; Brier score: 0.075
- Program errors: 0

The ECE and Brier values are descriptive only. With five all-correct samples, one model, and no
disjoint calibration/test split, they cannot establish calibration quality. The healthy detector's
fixed confidence of 0.4 accounts for much of the ECE despite the correct no-alert decision.

## Observations and limitations

The dependency ranker selected the ground-truth service in every fault run while retaining direct
and propagated service scores. The pod-failure margin was narrow (topology confidence 0.509), which
is useful evidence for future distractor and multi-fault experiments rather than a reason to hide the
result.

During the network-delay run, the wrk2 workload Job failed, but AIOpsLab's injected NetworkChaos
resource, collected cluster evidence, topology localization, and diagnosis remained complete; the
incident report itself contains no program error. All Chaos resources were absent after the suite,
and all 19 Hotel Reservation pods were Ready at the final health check.

This study uses one application, one host, one model, one run per condition, and four supported fault
classes. A credible comparative study still needs repeated injections at varied severity and load,
distractor/multi-fault conditions, multiple models, and a grouped calibration split with an untouched
test set.
