# Microservice network delay

## Typical signals

P95 latency and request-deadline errors increase while pods, CPU, and memory remain stable.

## Diagnosis

Correlate latency metrics, deadline logs, pod stability, and direct network-fault evidence. Exclude pod failure and resource exhaustion before assigning the network-delay root cause.

## Remediation

Review routes, network policy, service dependencies, and active Chaos rules. EvidenceOps does not automatically modify network configuration.

## Verification and rollback

Confirm P95 latency and request success return to baseline. Remove the reviewed fault or policy change if it caused the incident.
