# Platform repository file audit

Audited on 2026-10-10. Every retained source file is listed below. The platform
repo is the operational companion to the app: it holds operator guides, local
runtime tools, verification, monitoring integration and recovery exercises.
Kubernetes desired state belongs to boutique-gitops; AWS resources belong to
boutique-infrastructure; shared Jenkins delivery logic belongs to boutique-ci.

## What to use first

Follow [deploy-cicd-kind.md](deploy-cicd-kind.md) for the existing Debian laptop.
You do not need to run every script or install the two-cluster exercise. Read
[beginner-guide.md](beginner-guide.md) when you need the architecture explained.
The small runbooks are quick operational checklists, while the deployment guides
teach the commands and the reliability documents define objectives.

| Folder | Responsibility | Required for the primary laptop path? |
| --- | --- | --- |
| `docs/` and `docs/runbooks/` | Architecture, deployment, operations and recovery | Use the main guide and relevant references |
| `docs/validation-artifacts/` | Latest component evidence | Read-only evidence; not a deployment input |
| `scripts/` | Local setup, verification, backup and optional lab automation | Local Kubernetes secret setup and smoke/rollout verification are relevant |
| `tests/config/` and `tests/smoke/` | Regression checks and functional acceptance | Run applicable checks; smoke validates the deployed app |
| `local/` | Optional Compose and manual-image kind configuration | No, when using the existing cluster and published images |
| `local/production-lab/` | Optional two-cluster exercise | No; requires a suitable larger host |
| `monitoring/` | Compose observability, native validators and local mock fixtures | Optional after the app works; Kubernetes configuration comes from GitOps Helm |
| `monitoring/incident-bridge/` and `monitoring/servicenow/` | Optional incident adapter and instance-side reference | No; opt in explicitly |
| `workspace/` | Editor convenience | No |

## Cleanup and corrections

Removed five unused Kubernetes configuration copies: the homelab/nonprod/
production Prometheus files, `alloy-kubernetes.alloy` and `alertmanager.yml`.
They were consumed only by local validation; deployments already render the
GitOps Helm chart. Four were exact duplicates, while the generic Alloy copy
lacked the chart's namespace restriction. Native validation now extracts the
actual Helm ConfigMaps for all three profiles, both Slack-only and incident
modes, and checks their Prometheus rules, receiver routing and Alloy syntax.
No Kubernetes deployment resources were removed.

Removed unused `workspace/workspace.json`: no script or deployment consumed it.
The VS Code workspace remains useful. Its README now explains editor usage
instead of repeating the project overview and old validation claims.

Corrected `k8s-secrets.py`: API/auth failures now stop provisioning rather than
being mistaken for missing credentials. Existing secret names are read before
writes; a partial Redis pair is rejected. New objects use create rather than
apply, preventing accidental replacement after a concurrent creation. Existing
secrets, user volumes, private CA state and ignored credentials were preserved.
This helper remains for local development data; AWS uses its reviewed secret
manager/ESO configuration.

Retained Compose monitoring configs because they use container DNS, filesystem
paths and accelerated local mock routing. Kubernetes configs use pod discovery,
namespace scopes and environment-specific rules. These are distinct supported
runtime paths. Both the local notification drill and ServiceNow reference remain
optional, and neither is evidence of live external integration acceptance.
The long beginner guide, focused deployment guides and short runbooks serve
different reading/operational purposes; no retired delivery workflow is retained.

## Every retained file

| File | Role | Why retained |
| --- | --- | --- |
| [.gitignore](../.gitignore) | Source hygiene | Excludes credentials, databases, logs and tool caches; preserves private runtime state outside Git |
| [.trivyignore.yaml](../.trivyignore.yaml) | Optional adapter delivery | Explicit empty vulnerability-exception policy read by the shared service pipeline |
| [Jenkinsfile](../Jenkinsfile) | Optional adapter delivery | Builds incident-bridge through boutique-ci; seed creates this job only with ENABLE_INCIDENT_BRIDGE_BUILD=true |
| [LICENSE](../LICENSE) | Metadata | MIT distribution terms |
| [README.md](../README.md) | Start here | Current workflow and links to the main guide |
| [docs/architecture.md](architecture.md) | Architecture | Concise request flow, delivery boundaries and environment isolation |
| [docs/beginner-guide.md](beginner-guide.md) | Learning reference | Detailed explanations of all eight repos and deployment tracks; deliberately comprehensive |
| [docs/deploy-cicd-kind.md](deploy-cicd-kind.md) | Primary deployment | Step-by-step existing Debian/mega-local Jenkins and Argo flow; start here |
| [docs/deploy-local-kind.md](deploy-local-kind.md) | Optional manual deployment | Build/load local images and deploy Helm without CI; separate practice path |
| [docs/file-audit.md](file-audit.md) | Navigation | This complete file inventory and cleanup findings |
| [docs/jenkins-setup.md](jenkins-setup.md) | Delivery reference | Controller/agent, library, Pipeline seed and credential setup |
| [docs/monitoring.md](monitoring.md) | Operations | Metrics/logs/alerts, activation steps and incident triage |
| [docs/production-lab.md](production-lab.md) | Optional larger host | Two clusters, verified TLS, network policy and recovery exercises |
| [docs/recovery-targets.md](recovery-targets.md) | Reliability policy | RTO/RPO targets, backup cadence and actual implementation limits |
| [docs/repository-map.md](repository-map.md) | Navigation | Overview of current CI/platform responsibilities and retired components |
| [docs/runbooks/backup-restore.md](runbooks/backup-restore.md) | Recovery | Local backup/isolated restore commands and AWS restore boundary |
| [docs/runbooks/deployment.md](runbooks/deployment.md) | Operations | Short release/recovery checklist for operators |
| [docs/runbooks/disaster-recovery.md](runbooks/disaster-recovery.md) | Recovery | Manual reconstruction, data validation, write fencing and reviewed cutover |
| [docs/runbooks/incident-response.md](runbooks/incident-response.md) | Operations | Impact, mitigation, verification and incident record checklist |
| [docs/runbooks/jenkins-recovery.md](runbooks/jenkins-recovery.md) | Recovery | Controller home/key backup and isolated restore acceptance |
| [docs/runbooks/recovery-exercise.md](runbooks/recovery-exercise.md) | Recovery | Template for recording measured RTO/RPO and evidence |
| [docs/runbooks/rollback.md](runbooks/rollback.md) | Recovery | Protected GitOps history rollback and migration compatibility checks |
| [docs/runbooks/teardown.md](runbooks/teardown.md) | Operations | Stop/remove only intended resources while preserving data/backups |
| [docs/slo.md](slo.md) | Reliability policy | Eligible requests, PromQL, error budgets and measurement limitations |
| [docs/validation-artifacts/jenkins-runtime.json](validation-artifacts/jenkins-runtime.json) | Evidence | Latest isolated native Jenkins report; not a deployment configuration |
| [docs/validation-artifacts/summary.json](validation-artifacts/summary.json) | Evidence | Current check summaries and explicit untested boundaries |
| [docs/validation.md](validation.md) | Evidence | Passed component checks and outstanding live acceptance work |
| [local/compose.yaml](../local/compose.yaml) | Optional Compose | Four app services, Redis and PostgreSQL for local development |
| [local/kind.yaml](../local/kind.yaml) | Optional manual deployment | Small introductory kind cluster; does not replace mega-local |
| [local/production-lab/.gitignore](../local/production-lab/.gitignore) | Optional larger host | Keeps cluster credentials/CA state and downloaded caches private |
| [local/production-lab/kind-nonprod.yaml](../local/production-lab/kind-nonprod.yaml) | Optional larger host | Nonprod node/port/CNI configuration for dev and staging |
| [local/production-lab/kind-production.yaml](../local/production-lab/kind-production.yaml) | Optional larger host | Separate production learning cluster with its own port/subnet |
| [local/production-lab/versions.lock.json](../local/production-lab/versions.lock.json) | Optional larger host | Checksum-pinned tools/manifests and immutable controller images |
| [monitoring/alertmanager-local.yml](../monitoring/alertmanager-local.yml) | Optional Compose monitoring | Fast mock notification intervals and authenticated adapter receiver |
| [monitoring/alloy-local.alloy](../monitoring/alloy-local.alloy) | Optional Compose monitoring | Read captured application log files and ship to Loki without Docker socket access |
| [monitoring/capture-local-logs.sh](../monitoring/capture-local-logs.sh) | Optional Compose monitoring | Foreground Docker Compose log capture for Alloy |
| [monitoring/compose.yaml](../monitoring/compose.yaml) | Optional Compose monitoring | Prometheus, Alertmanager, Grafana, Loki, Alloy, adapter and internal mocks |
| [monitoring/grafana/dashboards/boutique.json](../monitoring/grafana/dashboards/boutique.json) | Optional Compose monitoring | Thirteen provisioned service/incident/log panels |
| [monitoring/grafana/provisioning/dashboards/dashboards.yml](../monitoring/grafana/provisioning/dashboards/dashboards.yml) | Optional Compose monitoring | Load the checked-in dashboard from its mounted path |
| [monitoring/grafana/provisioning/datasources/datasources.yml](../monitoring/grafana/provisioning/datasources/datasources.yml) | Optional Compose monitoring | Prometheus and Loki datasource identities matching the dashboard |
| [monitoring/incident-bridge/.dockerignore](../monitoring/incident-bridge/.dockerignore) | Optional adapter delivery | Allowlist build inputs; exclude local queues, reports and other files |
| [monitoring/incident-bridge/.gitignore](../monitoring/incident-bridge/.gitignore) | Optional adapter delivery | Ignore generated JUnit reports |
| [monitoring/incident-bridge/Dockerfile](../monitoring/incident-bridge/Dockerfile) | Optional adapter delivery | Separate dependencies/test/runtime targets with non-root runtime |
| [monitoring/incident-bridge/README.md](../monitoring/incident-bridge/README.md) | Optional adapter reference | Store/replica settings, delivery guarantees, credentials and recovery |
| [monitoring/incident-bridge/bridge.py](../monitoring/incident-bridge/bridge.py) | Optional adapter runtime | Authenticated ingestion, durable queue, lease/retry processing and ITSM delivery |
| [monitoring/incident-bridge/ci/test.sh](../monitoring/incident-bridge/ci/test.sh) | Optional adapter tests | Separate Jenkins test-stage entry point and report output |
| [monitoring/incident-bridge/ci/unittest-junit.py](../monitoring/incident-bridge/ci/unittest-junit.py) | Optional adapter tests | Convert stdlib unit-test results into Jenkins JUnit XML without runtime dependencies |
| [monitoring/incident-bridge/requirements.in](../monitoring/incident-bridge/requirements.in) | Optional adapter maintenance | Human-edited PostgreSQL dependency input for hash-lock regeneration |
| [monitoring/incident-bridge/requirements.txt](../monitoring/incident-bridge/requirements.txt) | Optional adapter reproducibility | Frozen dependency versions/hashes consumed by Docker; complements requirements.in |
| [monitoring/incident-bridge/run-postgres-tests.py](../monitoring/incident-bridge/run-postgres-tests.py) | Optional adapter integration | Disposable isolated real PostgreSQL checks, invoked by shared pipeline after runtime build |
| [monitoring/incident-bridge/test_bridge.py](../monitoring/incident-bridge/test_bridge.py) | Optional adapter tests | Seventeen SQLite/authentication/migration/delivery-race/health regressions |
| [monitoring/incident-bridge/test_postgres.py](../monitoring/incident-bridge/test_postgres.py) | Optional adapter integration | Five real PostgreSQL concurrency/lease tests; runner mounts this into runtime container |
| [monitoring/incident-bridge/test_receivers.py](../monitoring/incident-bridge/test_receivers.py) | Local test fixture | Two mock scripted-contract checks; run locally, not part of the seventeen-test adapter Docker target |
| [monitoring/init-local.py](../monitoring/init-local.py) | Optional Compose monitoring | Generate missing local-only mock credentials and Grafana password; preserve existing files |
| [monitoring/loki.yml](../monitoring/loki.yml) | Optional Compose monitoring | Filesystem Loki storage and seven-day retention using Compose volume paths |
| [monitoring/mock-receivers.py](../monitoring/mock-receivers.py) | Local test fixture | Mock Slack/ServiceNow contract; Compose, optional two-cluster helper and receiver tests use it |
| [monitoring/prometheus-local.yml](../monitoring/prometheus-local.yml) | Optional Compose monitoring | Scrape Compose service DNS names rather than Kubernetes pod discovery |
| [monitoring/render-validation.py](../monitoring/render-validation.py) | Monitoring validation | Extract six actual Helm configuration variants into temporary public files; replaces unused Kubernetes copies |
| [monitoring/rules.test.yml](../monitoring/rules.test.yml) | Monitoring validation | Native promtool expectations for aggregation, health, retries and missing incident targets |
| [monitoring/rules.yml](../monitoring/rules.yml) | Optional Compose monitoring | Compose alert/recording rules; Kubernetes has environment-specific Helm rules |
| [monitoring/servicenow/scripted-rest-resource.js](../monitoring/servicenow/scripted-rest-resource.js) | Optional ServiceNow reference | Authenticated ITSM lifecycle upsert with terminal resolution; requires instance-side ACL/index validation |
| [monitoring/test-routing.py](../monitoring/test-routing.py) | Monitoring validation | Native amtool checks for Slack-only defaults and optional incident routing |
| [monitoring/test_delivery.py](../monitoring/test_delivery.py) | Optional local integration | Firing/duplicate/resolved HTTP notifications against local mocks; never live receivers |
| [monitoring/validate.sh](../monitoring/validate.sh) | Monitoring validation | Native promtool/amtool/Alloy checks for Compose and rendered Helm configurations |
| [scripts/backup-local.sh](../scripts/backup-local.sh) | Optional Compose recovery | Create a PostgreSQL dump and verify archive readability; scheduling/off-host storage are separate |
| [scripts/init-local.sh](../scripts/init-local.sh) | Optional Compose | Generate missing local app secrets without replacing local/.env |
| [scripts/install-tools.sh](../scripts/install-tools.sh) | Operator bootstrap | Verified Linux amd64 CLI installation into caller-selected directory |
| [scripts/k8s-secrets.py](../scripts/k8s-secrets.py) | Primary local deployment | Create missing local app secrets; preserve existing credentials and fail on API/auth errors |
| [scripts/local-up.sh](../scripts/local-up.sh) | Optional Compose | Initialize, build/start the app and run functional smoke checks |
| [scripts/production-lab-tls-test.py](../scripts/production-lab-tls-test.py) | Optional larger-host validation | Real disposable PostgreSQL/Redis client tests for trusted CA, hostname and plaintext rejection |
| [scripts/production-lab.py](../scripts/production-lab.py) | Optional larger host | Explicit-context bootstrap, private TLS/secrets, verifier access, readiness/policy/restore drills |
| [scripts/restore-drill.sh](../scripts/restore-drill.sh) | Optional Compose recovery | Restore orders to a throwaway database and report row count; preserves live boutique DB |
| [scripts/test_production_lab.py](../scripts/test_production_lab.py) | Optional helper validation | Thirteen safety regressions for contexts, credential preservation, pins and verifier permissions |
| [scripts/wait-for-deployment.py](../scripts/wait-for-deployment.py) | Delivery verification | Read-only trusted GitOps ancestry, Argo source/revision and all-service rollout verification |
| [tests/config/test_k8s_secrets.py](../tests/config/test_k8s_secrets.py) | Deployment safety validation | Four regressions for API failures, partial Redis state, existing secrets and matching new credentials |
| [tests/config/test_manifests.py](../tests/config/test_manifests.py) | Deployment validation | Local/laptop Helm profiles, dependency endpoints and production rejection |
| [tests/config/test_monitoring_helm.py](../tests/config/test_monitoring_helm.py) | Monitoring validation | Actual Helm scope/RBAC, persistence, incident defaults, production/lab selection and Argo configuration |
| [tests/config/test_promotion.py](../tests/config/test_promotion.py) | Delivery validation | Immutable image/chart selection, same-artifact promotion and protected-history rollback |
| [tests/config/test_wait_for_deployment.py](../tests/config/test_wait_for_deployment.py) | Delivery validation | Real temporary Git ancestry and mocked native Argo/Deployment failure/wait states |
| [tests/smoke/smoke.py](../tests/smoke/smoke.py) | Functional acceptance | Twelve HTTP journey checks including ownership, validation, pricing and idempotency; report only succeeds after all pass |
| [workspace/README.md](../workspace/README.md) | Optional editor convenience | How to open all eight sibling repositories; points to current deployment guides |
| [workspace/boutique.code-workspace](../workspace/boutique.code-workspace) | Optional editor convenience | VS Code multi-folder view; no runtime or pipeline dependency |

## Validation and boundaries

```bash
# From boutique-platform; requires the reviewed Helm tool and PyYAML.
python3 -m unittest discover -s tests/config -v
python3 scripts/test_production_lab.py -v
bash monitoring/validate.sh
python3 -m unittest discover -s monitoring/incident-bridge -p 'test_bridge.py' -v
python3 -m unittest discover -s monitoring/incident-bridge -p 'test_receivers.py' -v
# Real isolated PostgreSQL integration; build this runtime image using the adapter README first.
python3 monitoring/incident-bridge/run-postgres-tests.py --image boutique-incident-bridge:test
```

Configuration validation includes exact image/chart promotion, rollback and
rollout guards; monitoring validation uses native Prometheus, Alertmanager and
Alloy binaries in pinned containers. Python/shell syntax, Compose structure,
JSON/YAML parsing, Markdown links and the complete inventory are also checked.
No test in this audit sends live Slack notifications, creates ServiceNow tickets,
changes laptop clusters or applies AWS resources.

The PostgreSQL integration test uses its own disposable database/network and
removes only those resources. Full Kubernetes deployment, live delivery,
monitoring-wide HA, scheduled/off-host backups and complete DR acceptance remain
separate target-host work. Existing [validation.md](validation.md) records the
component evidence and these limits; [recovery targets](recovery-targets.md) and
[the DR runbook](runbooks/disaster-recovery.md) describe objectives to measure.


## Shared observability extension

The shared Helm profile now observes all three application namespaces, with
namespaced workload-state permissions, digest-pinned exporters and production
SLO burn alerts. `monitoring/shared-rules.test.yml` tests sustained errors, healthy
traffic, low-volume safeguards, non-production exclusion and all twelve missing
service/environment combinations. The render validator and routing checks cover
both shared Slack receivers and the retained optional ServiceNow path. See the
GitOps monitoring README for live verification and unimplemented boundaries.
