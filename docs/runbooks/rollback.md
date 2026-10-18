# Application rollback

Stop competing releases and identify a previous protected-main GitOps commit
whose service image/chart passed rollout and functional smoke checks.
Run `Release Operations/boutique-rollback` with TARGET, SERVICE and the full
GITOPS_COMMIT.
It restores only that service's exact digest/chart, validates manifests and opens
a new PR. It rejects unmerged history and mutable images.

Review database migration compatibility and the diff before approving/merging.
Argo syncs the merged state; verify readiness and run smoke tests. Retain the
original build, recovery PR/merge and test results. No signed rollback record,
verification age gate or environment lock is required by this implementation.

Do not reset protected main or rely on kubectl set image/rollout undo: Argo
reconciles Git. Data loss needs the separate backup/restore procedure. Measure
elapsed time and recovered writes against the documented RTO/RPO.
