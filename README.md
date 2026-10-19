# Boutique runtime and operations

The single project guide is [boutique-ci/DOC.md](https://github.com/Local-Narx-Space/boutique-ci/blob/main/DOC.md). It explains architecture,
CI/CD, Docker, Helm/GitOps, Python automation, setup, monitoring and recovery.

This repository contains runtime assembly, the active deployment verifier,
scoped verifier credential bootstrap, local Kubernetes backups and optional
Compose/monitoring test fixtures. The existing kind-mega-local cluster hosts
dev, QA and production namespaces; separate namespaces do not provide host HA.

```bash
python3 -m unittest discover -s tests/config -v
```

Run `scripts/local-up.sh` for disposable Compose development. Use the guide for
Kubernetes deployment, backup/restore and troubleshooting. Operational commands
need the intended context and existing secrets; never replace a live cluster
or regenerate database/session keys as a cleanup step.

The retired two-cluster bootstrap and unsupported incident-adapter Jenkinsfile
are removed. `local/controller-versions.lock.json` retains the checksummed
installation metadata used by the guide. The optional incident adapter remains
available for local tests; it is not an active fifth Jenkins service.
