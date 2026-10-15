# Microservice network packet loss

## Typical signals

Packet loss, request timeouts, and error rates rise together while pods remain Ready and restart counts stay stable.

## Diagnosis

Align packet-loss metrics, HTTP timeout logs, pod state, and the incident window. Direct active network-fault evidence outranks generic resource metrics.

## Remediation

Review NetworkPolicy, service routing, node networking, and active Chaos rules. EvidenceOps provides a recommendation only and does not automatically change network policy.

## Verification and rollback

Confirm packet loss, timeout rate, and request success return to baseline. Remove a fault or policy change only after operator review.
