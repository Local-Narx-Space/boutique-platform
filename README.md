# Boutique runtime and operations

Start with [the existing Debian/kind CI/CD guide](docs/deploy-cicd-kind.md).
The flow is reviewed main → Jenkins tests/scans/build/publish → GitOps PR →
Helm/manifest checks and human merge → Argo CD → rollout and smoke verification.
Promote the same image digest/chart from dev to staging to production.

The [beginner architecture guide](docs/beginner-guide.md) explains the eight
repos, request flow, file types and operating model. [Jenkins setup](docs/jenkins-setup.md)
uses one controller, a private rootless build agent, a version-pinned shared
library and a Pipeline seed. It requires no custom signing/evidence system.

For your existing `mega-local` cluster, dev uses published GHCR images, local
PostgreSQL/Redis and loopback port 8088. Do not replace its existing controllers.
The [manual local-image exercise](docs/deploy-local-kind.md) remains optional.
`./scripts/local-up.sh` provides fast Compose application development.

[Monitoring](docs/monitoring.md) covers Prometheus, provisioned Grafana dashboards,
Alertmanager/Slack, Loki/Alloy and optional ServiceNow. Incident workers and their
queue are disabled by default in Kubernetes. Keep SLOs, recovery targets and
restore drills; production availability claims require measured results.

The [two-cluster exercise](docs/production-lab.md) is optional on a larger host.
AWS Terraform remains a separate reference under boutique-infrastructure, with
VPC/EKS/managed data, ESO/IRSA, CloudWatch and CloudTrail. No paid resources are
provisioned by the local path. Production should have separate cluster/account,
credentials, data and backups; one laptop does not provide that isolation.

```bash
python3 -m unittest discover -s tests/config -v
python3 scripts/test_production_lab.py -v
```

See [validation.md](docs/validation.md) for executed checks and live acceptance
work. [SLOs](docs/slo.md), [RTO/RPO](docs/recovery-targets.md) and
[DR](docs/runbooks/disaster-recovery.md) remain part of the operating model.

See [validation and acceptance](docs/validation.md) for automated checks and the
live exercises that still need to be run on the target laptop.
