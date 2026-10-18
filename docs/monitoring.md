# Shared monitoring on the current cluster

The shared homelab Helm profile now covers **dev, staging and production**, plus
monitoring self-metrics/logs. Argo CD still manages one monitoring Application;
the environment ApplicationSet manages business deployments separately.

It adds namespace-scoped kube-state-metrics, node-exporter, TLS-verifying public
readiness probes, production SLO burn alerts, and three Grafana dashboards:
Boutique operations, Boutique platform and monitoring, Boutique production SLOs.
ServiceNow is optional and its panels/alerts are absent when disabled.

All alerts use the existing Slack channel with environment-labelled titles.
Production and non-production have separate logical receivers for timing and
routing. No extra webhook is needed for the selected one-channel setup.

Reapply boutique-gitops/applications/monitoring-homelab.yaml before syncing the
monitoring Application, because its AppProject gains staging/production RBAC
scope. The current stack preserves PVCs and existing credentials. See the
[shared chart operations guide](https://github.com/Local-Narx-Space/boutique-gitops/blob/main/monitoring/README.md#shared-cluster-observability)
for rollout checks, retention, exporter image provenance and limitations.

Public probes check /health/ready without placing orders. Configure all three
Cloudflare routes; missing routes correctly alert. Because the prober and
Alertmanager run in the same cluster, an independent remote check/dead-man
receiver is still needed for full-cluster failure detection. Kubelet/cAdvisor
per-container resource usage and actual per-PVC free space are not configured.
On kind, node metrics may represent the shared Debian kernel rather than
independent physical node capacity. The single-replica stack is not HA.

## Existing Compose and cloud/lab workflows

> Kubernetes defaults to Prometheus/Grafana, Alertmanager/Slack, Loki/Alloy.
> ServiceNow and incident queue workers are optional (`incidentBridge.enabled=true`).
> The integration, queue and mock exercises below require that explicit opt-in.
> Default Alertmanager only requires `slack_webhook` in monitoring-integrations.

# Monitoring and incident response

This lab includes Prometheus application metrics, versioned Grafana dashboards, Alertmanager, Loki and Grafana Alloy. It exercises the same alert-to-response workflow used in production while keeping the default stack affordable. External receivers are prepared; no real Slack messages or ServiceNow incidents have been sent.

## Start locally

From `boutique-platform`:

```sh
python monitoring/init-local.py
# First start the application using scripts/local-up.sh.
docker compose --env-file local/.env -f local/compose.yaml -f monitoring/compose.yaml up -d --build
bash monitoring/capture-local-logs.sh
```

The log capture process runs in the foreground; stop it with Ctrl-C. Start it in a second terminal. Alloy reads these files and ships them to Loki without a mounted Docker socket. Ports bind only to loopback: Grafana 8085, Prometheus 9090, Alertmanager 9093, mock receivers 18080. Grafana user is `admin`; retrieve the generated password locally from `monitoring/.secrets/grafana_password` and keep it out of chat, logs and Git. Admin password changes require Grafana's password-reset flow once its persistent database already exists. Do not use the mock receiver outside local tests; it is unauthenticated and ephemeral.

## Signals and routing

The provisioned Boutique dashboard shows service availability, request rate, error ratio, HTTP p95, JVM/frontend memory, firing alerts, incident backlog and logs, with an environment selector. Metrics labels contain bounded route templates; session IDs, request IDs and product IDs are never metric labels. Logs retain request IDs as log fields for investigations. Avoid putting credentials or personal data into application logs. Logs are retained for seven days by default; set disk quotas and review retention for your workload.

Kubernetes Prometheus discovers individual application and incident bridge pods, so replicas do not share a counter stream behind a load-balanced Service. Bridge discovery is restricted to the monitoring namespace and includes running pods even when readiness fails, preserving visibility into stalled workers. Namespace-scoped RBAC permits only pod discovery, without Secret access. Missing-target rules cover services and the incident bridge when no targets exist. Shared PostgreSQL queue gauges use the maximum per cluster/environment instead of summing identical observations from both workers; heartbeat and scrape health remain per pod.

Prometheus rules detect unavailable services, sustained error ratio with a traffic floor, sustained latency, failed rule evaluation, failed Alertmanager delivery, aged incident queues, durable dead letters, stale worker heartbeats and unavailable bridge replicas. Alertmanager groups by cluster, environment, service and alert name; warning alerts are inhibited when a matching critical alert fires. Repeated notifications are limited to four hours. Local mocks use one-second grouping delay, five-second grouping interval and one-minute repeats to make drills quick; Kubernetes uses thirty seconds, five minutes and four hours. All configured alerts go to Slack; critical **production** application alerts also go to ServiceNow. Delivery-path alerts carry `incident_delivery="disabled"` and remain Slack-only, preventing bridge/queue failures from recursively enqueueing incidents into the failing path. Maintenance silences must expire and record an owner/reason. The homelab has no on-call paging provider or external dead-man receiver configured.

## Slack activation

Create an incoming webhook for a dedicated alert channel using your Slack workspace's approved app process. Store its URL in your secret manager as `slack_webhook`; never commit it. Alertmanager reads a mounted secret file. Replace the local mock URL only when you intend to enable live notifications. Receiver tests supplied here use mocks.

## ServiceNow activation

Use a free ServiceNow developer instance with ITSM Incident Management for the homelab. It teaches incident creation, correlation, resolution, retry handling and least-privilege API access without an ITOM Event Management license. Developer instances can sleep/reset and are not production services.

Create a dedicated integration user with only the ACLs required to query/create/update integration-owned incidents. Do not give it admin. Set `SERVICENOW_URL` to the HTTPS instance base URL, `SERVICENOW_USERNAME` to that user and `servicenow_password` through the secret manager. `SERVICENOW_CLOSE_CODE` must match your instance's configured choice value, and required incident fields/business rules may require adapter adjustments. TLS certificate verification remains enabled. The Table API adapter uses Basic auth over HTTPS; a production deployment should prefer your organization's OAuth credential flow.

The local default `SERVICENOW_MODE=table` queries `correlation_id` before creation and resolves the existing incident when the alert resolves. The key hashes fingerprint plus alert start time, so a new alert cycle gets a new incident. Table API lookup followed by insert **cannot guarantee exactly-once creation** during ambiguous timeouts/concurrent writes. The production profile selects `SERVICENOW_MODE=scripted`. Review `monitoring/servicenow/scripted-rest-resource.js`, create its lifecycle table, incident correlation field and required unique indexes, and install its authenticated Scripted REST resource before activating it. Lifecycle tombstones preserve terminal resolution, including resolution arriving before firing. This reference script must be validated in your instance; it has not been remotely tested.

The bridge authenticates Alertmanager batches and commits the whole batch before acknowledging. Its affordable local/homelab profile uses SQLite WAL on a dedicated volume and exactly **one replica**. The production profile uses a dedicated PostgreSQL database and **two replicas**, with transactional `FOR UPDATE SKIP LOCKED` claims, 60-second leases, revision-safe acknowledgements, verified TLS, rolling updates and a disruption budget. A resolution arriving during a firing delivery stays pending; a crashed worker's lease can be recovered without letting the stale owner acknowledge another worker's revision.

Delivery remains at least once because a remote write can commit before its response is lost. Retries use capped exponential backoff and become durable dead letters after 12 failures; an authenticated requeue endpoint supports operator recovery. Completed cycle rows remain deduplication tombstones. Back up the complete queue and Alertmanager silences, monitor storage, and define a reviewed archival policy. Two workers do not make a single database highly available: the production lab's PostgreSQL StatefulSet is single replica, while the AWS reference uses a dedicated managed database. See [adapter configuration and recovery](../monitoring/incident-bridge/README.md).

## Kubernetes profiles and secrets

Render the `boutique-gitops/monitoring` Helm chart with `python3 boutique-gitops/scripts/render-monitoring.py homelab`, `nonprod`, or `production` from the workspace root, or configure its Argo Application. Add `--lab` for the nonprod/production learning clusters. Their target lists are dev only, dev+staging, and production only, avoiding phantom alerts. Before applying, provision Secret `monitoring-integrations` in namespace `monitoring` with keys `slack_webhook`, `webhook_token`, `servicenow_password`, and `grafana_password`. The production profile additionally requires Secret `monitoring-incident-queue`, with `QUEUE_DATABASE_URL` and `postgresql_ca`; the URI must use `sslmode=verify-full&sslrootcert=/run/queue-ca/ca.crt`. Configure the actual database subnet egress policy for AWS. Set the ServiceNow URL/user in the reviewed Helm deployment values; select `incidentBridge.image.digest` and clear `incidentBridge.image.tag` in the cloud profile values using the digest from the trusted Jenkins release job (`SERVICE=incident-bridge`, source repository `boutique-platform`). Promotion of that monitoring image is a reviewed GitOps change. The app promotion job remains limited to the four app services.

Core pod secret mounts expose only the receiver-specific keys needed by that component. For production use, enable internal webhook TLS or service-mesh mTLS as well as network isolation; the homelab webhook uses cluster-internal HTTP with a bearer token.

All core pods use non-root identities, resource limits and persistent storage. Grafana anonymous access and signup are disabled; services are internal ClusterIP without public ingress. Add SSO and TLS before exposing them. Alloy's host log collector has an isolated namespace with a Pod Security exception for read-only hostPath access and root file reading; it drops capabilities and cannot escalate privileges. Its Kubernetes permission is namespace-scoped read-only pod metadata, not Secrets. Review this exception and enforce egress restrictions with your cluster CNI. Log position state is ephemeral on node restart, so duplicate replay is possible.

For HA monitoring and Kubernetes/node metrics see `boutique-gitops/monitoring/production-reference`. Prometheus, Alertmanager, Grafana and Loki in the hand-written stack remain single replica. The incident adapter has a separate two-worker PostgreSQL profile; this does not establish HA for the entire monitoring stack. Production object storage, database availability, backups, SSO, paging and dead-man detection still require deployment and operational validation.

## Service down

1. Confirm scope in Grafana and Prometheus target health; distinguish a scrape/network failure from an application failure.
2. Inspect rollout status, pod readiness, restarts, recent deployments and dependencies. Review logs for the same environment/service and request ID.
3. Roll back through the GitOps repository to the previous tested digest when a deployment is responsible. Avoid untracked live edits.
4. Confirm error rate/latency and the incident resolution; record the cause and follow-up in the incident.

For incident backlog: check bridge readiness, PostgreSQL or SQLite availability/capacity, dead-letter count, receiver auth/ACLs, HTTPS connectivity and instance availability. Fix the dependency; retryable records resume automatically, while exhausted dead letters require the documented authenticated requeue. Do not delete the queue to clear an alert. For a failed Slack webhook, rotate it through secret settings and reload/restart Alertmanager, then verify delivery in an approved channel.

## Validation

`python -m unittest discover -s monitoring/incident-bridge -p 'test_*.py' -v` covers SQLite retry, schema migration, authentication, health and resolution races. The dedicated PostgreSQL integration runner in the adapter directory tests independent worker processes, locked-row skipping, leases and concurrent resolution against a disposable real database. A two-container HTTP drill created twenty unique mock incidents from duplicate concurrent batches, resolved all twenty and verified that delayed firing did not reopen them.

Promtool rule tests verify shared-queue aggregation, backlog thresholds, dead letters, individual worker health and missing targets. Amtool route tests verify that delivery-path alerts reach Slack only while critical production application alerts reach both receivers. These tests send no external notifications. Local runtime tests establish the exercised worker behavior, not live ServiceNow ACL/business-rule compatibility, a successful Kubernetes deployment, or monitoring-wide HA readiness.

## Reliability objectives

See [slo.md](slo.md) for request eligibility, frontend-only PromQL, error budgets and measurement limits. Current seven-day Prometheus retention cannot support a complete 30-day scorecard. Automated SLO burn-rate alerts and continuous external journey probes remain future activation work.

Run `bash monitoring/validate.sh` with Helm, PyYAML and Docker available to check
Compose configuration and all three actual Kubernetes Helm profiles, including
Slack-only defaults and optional incident routing. Kubernetes configuration is
maintained in boutique-gitops; this repo holds no extra Kubernetes config copies.
