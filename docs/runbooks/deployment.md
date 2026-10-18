# Deployment and rollback runbook

Use [the main guide](../deploy-cicd-kind.md) and [Jenkins setup](../jenkins-setup.md).
Before first sync, select all four published image digests/charts and provision
application secrets/data. A publishing build does not prove a live deployment.

Routine release: protected main → tests/scans/build/publish → dev GitOps PR →
boutique/gitops-validation → independent review/merge → Argo sync → rollout and smoke.
Promote unchanged image/chart from dev to staging, staging to production.
The reviewer confirms the preceding environment passed checks and migrations
are compatible; Git selection is not signed proof of execution.

For recovery, stop competing releases, select a known-good protected GitOps
commit and run `Release Operations/boutique-rollback`. Review the restored service package/digest and
migration compatibility, merge, sync and rerun smoke. Do not reset main or use
kubectl image changes as a permanent GitOps rollback. Data loss requires the
[DR procedure](disaster-recovery.md), not only app rollback.

Keep build URLs, PR/merge commits, Argo revision, rollout/smoke results, alerts and
restore timings. Escalate failures before declaring the environment healthy.
