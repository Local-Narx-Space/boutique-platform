> Optional larger-host exercise. The existing 8 GiB Debian/kind deployment uses
> [deploy-cicd-kind.md](deploy-cicd-kind.md), not this two-cluster bootstrap.
> Current delivery uses reviewed GitOps PRs and ordinary rollout/smoke reports;
> signed-release/evidence and aggregate bootstrap jobs have been removed.

# Production delivery lab

Follow [the main CI/CD-first deployment guide](deploy-cicd-kind.md) for the
ordered setup. Cluster/controller bootstrap prepares the platform; Jenkins
publishes the first published releases before Argo deploys the app. A prior
Compose or manual Kubernetes application deployment is optional.

Keep one repository per service. This lab uses the same reviewed releases, immutable image digests, Helm chart/image selections and verification gates as the AWS reference. Two independent Kubernetes clusters separate dev/staging from production; it does not create AWS resources or replace the existing Docker Compose installation.

| Boundary | Configuration |
|---|---|
| Nonproduction cluster | `kind-boutique-nonprod`: dev and staging namespaces, two workers |
| Production cluster | `kind-boutique-production`: production namespace, two workers |
| Network enforcement | Checksum-pinned Calico, default-deny application policies, executable allow/deny drill |
| Delivery | Digest-only releases, Argo CD per cluster, restricted AppProjects, existing Helm chart/image selections |
| Ingress and data | Traefik TLS, cert-manager certificates, CA/hostname verification for PostgreSQL and Redis |
| Verification identity | Namespace read-only service account plus Argo Application read access; no Secret read or deployment permission |
| Monitoring exercises | Prometheus/Grafana/Loki/Alloy; internal Slack/ServiceNow mock receivers; dedicated TLS PostgreSQL incident queue in production |

The single control plane and single-replica databases remain explicit availability limits of this affordable runtime. Two workers let you exercise scheduling, rolling updates, PodDisruptionBudgets and worker failure. They do not provide control-plane or database HA. EKS and managed databases are the cloud reference for those concerns. Local CA private keys and kind control-plane disks must be protected; Kubernetes Secrets in this local profile are not a replacement for the AWS Secrets Manager/KMS workflow.

## Host prerequisites

Use a Linux Docker host/VM with working privileged containers, writable host cgroups, kernel networking modules, Docker Compose, Python 3, OpenSSL and kubectl 1.34. Allow at least 16 GiB RAM (24–32 recommended) and 30 GiB free Docker storage. VFS needs substantially more disk; allow at least 60 GiB free or use a supported overlay2 host. Docker runs on the host, not inside an unprivileged application container.

From `boutique-platform`, install the shared CLI prerequisites, then the lab's checksum-locked kind version:

```bash
./scripts/install-tools.sh "$PWD/.tools"
export PATH="$PWD/.tools:$PATH"
python3 scripts/production-lab.py fetch-tools
python3 scripts/production-lab.py fetch-manifests
python3 scripts/production-lab.py doctor
```

The artifact lock contains upstream manifest SHA256 values, immutable upstream Git commit URLs where available, and every controller image digest. Cached bytes are checked again on every use. The Traefik installation is the official rendered chart release; the lab applies a reviewed NodePort/replica patch. Argo's Redis image uses Docker Hub's official Redis mirror, with its own verified digest, to avoid reliance on the ECR mirror. Update locks through a reviewed dependency change, not by changing tags in place.

`doctor` does not pull images, create clusters, stop containers or delete volumes. A failing preflight stops bootstrap. If nested kubelet fails, use a supported Linux VM/host; do not disable probes, network policies or certificate verification to make the result look successful. Failure logs are exported beneath the ignored private state directory when kind can export them.

## Bootstrap both foundations

```bash
python3 scripts/production-lab.py bootstrap --cluster nonprod
python3 scripts/production-lab.py bootstrap --cluster production
```

All kubectl operations specify the intended cluster context. Operator kubeconfigs, distinct per-cluster CA keys, and local restore backups live under `local/production-lab/.state/`, ignored by Git and protected by private directory/file modes. Never use an operator kubeconfig as a Jenkins build credential. Existing CA/credential state is preserved on reruns; partial credential state or changed cluster trust blocks setup rather than silently resetting passwords.

Add these names to the operator machine's hosts file:

```text
127.0.0.1 dev.boutique.test staging.boutique.test production.boutique.test
```

The frontend origins are `https://dev.boutique.test:8443`, `https://staging.boutique.test:8443` and `https://production.boutique.test:9443`. The matching CA files are `.state/nonprod/ca/ca.crt` and `.state/production/ca/ca.crt`. Import the relevant CA into your browser trust store if using the UI; automated smoke tests use that CA directly and retain hostname verification. Only loopback TLS ports are exposed by the kind configuration. A Jenkins verifier on another machine needs private network routing/DNS and trusted TLS endpoints; `127.0.0.1` inside a separate Jenkins container refers to that container, not this host.

## Prepare publication, credentials and releases

Argo CD reads the published GitOps repository. Prepare the cluster foundations,
then publish reviewed service main through the normal Jenkins gates. Select the
four dev artifacts through ordinary `Deployments/boutique-deploy-dev` PRs. Do not sync an
incomplete environment. ServiceNow adapter publication is optional.
`lab-profiles/ENV/values.yaml` adds local data/TLS without replacing the selected
image digest or packaged chart. See [current delivery](deploy-cicd-kind.md).

Use private files (`chmod 600`) for a read-only GitOps repository token and a GHCR `read:packages` token. Prefer short-lived GitHub App credentials and rotate them separately from the build/publish identity. Do not put tokens in command arguments, source files or chat.

```bash
python3 scripts/production-lab.py secrets --cluster nonprod \
  --repo-token-file /secure/gitops-read-token \
  --registry-token-file /secure/ghcr-read-token --registry-user YOUR_GITHUB_OWNER
python3 scripts/production-lab.py secrets --cluster production \
  --repo-token-file /secure/gitops-read-token \
  --registry-token-file /secure/ghcr-read-token --registry-user YOUR_GITHUB_OWNER
```

Omit `--repo-token-file` only for a public GitOps repository. Generated Redis, application session, application PostgreSQL, Grafana and integration fixture credentials are independent. Production incident queue credentials belong to a separate database/user in the monitoring namespace. Never reuse the orders database for the incident queue.

Existing secrets are preserved rather than rotated implicitly. To rotate an existing integration token/password, update the corresponding Kubernetes Secret using your secret-management process and reconcile its clients. Changing a PostgreSQL Secret alone does not change the password stored in an initialized database. Keep CA keys backed up securely; changing cluster trust is a coordinated operation. Cert-manager renews the leaf certificates, but the locally created one-year root CA requires a planned rotation.

## Connect Argo and verify delivery

After all four dev selection PRs are reviewed/merged:

```bash
git -C ../boutique-gitops pull --ff-only
python3 scripts/production-lab.py deploy --cluster nonprod --environment dev
python3 scripts/production-lab.py readiness --cluster nonprod --environment dev
python3 scripts/production-lab.py verify --environment dev
```

Promote each service to staging with `Deployments/boutique-deploy-qa`, confirming dev smoke
results before review. Merge all four first staging selections before activating:

```bash
git -C ../boutique-gitops pull --ff-only
python3 scripts/production-lab.py deploy --cluster nonprod --environment staging
python3 scripts/production-lab.py readiness --cluster nonprod --environment staging
python3 scripts/production-lab.py verify --environment staging
```

Repeat staging-to-production promotion with independent production review;
select all four releases before activating production:

```bash
git -C ../boutique-gitops pull --ff-only
python3 scripts/production-lab.py deploy --cluster production --environment production
python3 scripts/production-lab.py readiness --cluster production --environment production
python3 scripts/production-lab.py verify --environment production
```

Retain ordinary Argo/rollout/smoke reports and the GitOps merge commit. The helper
rejects placeholder/mutable app images. It does not publish releases, approve PRs
or sign verification records. TLS origins/CAs and scoped kubeconfigs are exported
for the environment deployment job's `ACTION=verify`; no additional agent label is required.

## Monitoring delivery exercises

After releasing/promoting the incident adapter image, replace its placeholder with the tested immutable digest in the monitoring manifests and commit that GitOps change. Then:

```bash
python3 scripts/production-lab.py deploy --cluster nonprod --monitoring
python3 scripts/production-lab.py deploy --cluster production --monitoring
python3 scripts/production-lab.py readiness --cluster nonprod --monitoring
python3 scripts/production-lab.py readiness --cluster production --monitoring
```

These profiles intentionally route to the internal mock Slack/ServiceNow receivers. The production profile still uses the scripted ServiceNow upsert lifecycle and the shared PostgreSQL queue for two adapter replicas; it does not create real tickets or send real messages. Mock receiver state is disposable and loses captured events on restart; the delivery queue is persisted separately. Follow `monitoring.md` for live credentials, network rules and an explicitly authorized live notification test. A reachable mock receiver is not proof of live paging.

## Repeatable failure and recovery drills

Run these against dev first, then repeat the workflow through the environment gates:

```bash
python3 scripts/production-lab.py policy-drill --environment dev
python3 scripts/production-lab.py restore-drill --environment dev
python3 scripts/production-lab-tls-test.py
python3 scripts/test_production_lab.py -v
```

The policy drill proves the permitted ingress namespace can reach the same frontend IPv4 Service that an unrelated namespace cannot reach. It uses the IP directly so DNS failures cannot masquerade as policy enforcement. Only its uniquely named temporary Jobs/namespace are removed.

The restore drill produces a private logical backup, restores into a uniquely named disposable database, verifies order count against the backup window and successful Flyway history, records elapsed restore time, and drops only that disposable database. It preserves the live database and PVC. Backups contain application data; encrypt them before exporting and establish retention. This is a database restore test, not a point-in-time recovery or a full disaster-recovery test.

The TLS test uses existing tested container images, isolated Docker networking and disposable data. It validates CA/hostname acceptance and rejection for both clients plus PostgreSQL/Redis TLS key permissions. It does not pull images, touch existing Compose containers/volumes or send external notifications.

Also execute the reviewed GitOps rollback workflow after a deliberately failed candidate, confirm reviewers require the preceding environment’s successful rollout/smoke reports before promotion, restore Jenkins from its configuration/backup runbook, and restart incident adapter replicas while notifications are pending. Capture evidence and recovery times instead of declaring the platform complete because configuration files exist.

## Validation boundary

The helper's safety tests and rendered configurations are covered in
[validation.md](validation.md). Component results do not establish installed
controllers, Argo reconciliation, enforced networking or measured recovery.
The doctor checks the actual host before creating clusters; execute the optional
exercise on a supported larger host and retain live rollout/smoke results.

## Recovery acceptance

Use [recovery-targets.md](recovery-targets.md) and the [DR runbook](runbooks/disaster-recovery.md) to record end-to-end RTO/RPO. The existing isolated `restore-drill` measures only a database step; it does not prove host/cluster disaster recovery. Recovering on a separate host with off-host backups is the intended quarterly exercise.
