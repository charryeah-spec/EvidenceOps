# Application image misconfiguration

## Typical signals

Request error rate rises abruptly while pods may remain Running and Ready. The Deployment image differs from the known-good version.

## Diagnosis

Correlate HTTP errors, pod readiness, the Deployment image, and service logs. A healthy-looking pod running a known-faulty image is a configuration root cause, even if it later produces restart symptoms.

## Remediation

Restore the affected Deployment to the approved known-good image. Preserve the current image, require human approval, and perform a server-side dry run first.

## Verification and rollback

Confirm rollout completion, Ready replicas, valid endpoints, the expected image, and recovery of application health. If recovery fails, restore the previously captured image and retain the incident evidence.
