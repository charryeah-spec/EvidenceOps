# Pod failure and repeated container restarts

## Typical signals

Pods enter CrashLoopBackOff, Error, or Unknown states; restart counts rise; Kubernetes reports BackOff events; and logs show unexpected termination or failed health probes.

## Diagnosis

Correlate pod phase, `containerStatuses`, restart count, readiness probes, and events. Treat restart symptoms as the root cause only when stronger configuration or image evidence is absent.

## Remediation

After checking configuration and dependencies, restart only the allowlisted Deployment. Require a server-side dry run and explicit human approval.

## Verification and rollback

Wait for rollout completion and confirm Ready replicas, stable restart counts, endpoints, and recovered health signals. If the new replicas fail, undo the rollout and preserve the failed pod logs.
