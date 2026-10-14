# Validation evidence

The current machine-readable [summary](validation-artifacts/summary.json) and
[isolated Jenkins report](validation-artifacts/jenkins-runtime.json) describe the
simplified delivery code. Superseded reports are available in Git history rather
than mixed with current acceptance evidence.

## Passed component checks

| Check | Result and boundary |
| --- | --- |
| Configuration tests | 48 passed: secret preservation, exact image/chart promotion, rejected unsafe changes/unmerged rollback, Argo history/health checks, laptop profile and optional monitoring |
| Optional two-cluster helper tests | 13 safety tests passed; no live cluster created |
| Helm/schema validation | 14 profiles, 339 resources: 327 schema-valid, zero invalid/errors, 12 explicit CRD skips |
| Candidate-input handling | Protected validation passed with candidate scripts deliberately broken; candidate scripts were not executed, symlinks rejected and packaged chart inputs inspected |
| Jenkins runtime | Jenkins 2.580.1 loaded all 79 checksum-locked plugins; JCasC had zero warnings |
| Jenkins job definitions | Sandboxed Job DSL generated nine default jobs and ten with the optional adapter enabled, with zero custom method approvals; native deserialization confirmed main-only discovery and initial automatic-build suppression |
| Runtime tool integrity | 7 tests passed for tool verification/install helpers |
| Pipeline syntax | Jenkins accepted the shared service pipeline, seed, manifest validation, promotion, rollback, verifier and infrastructure definitions |
| Separate service tests | Frontend 3, catalogue 5 (including 3 report-converter tests), cart 6, orders 2 and optional adapter 17 passed in non-root disposable test containers; all five runtime images built separately |
| Test failure gates | Intentional failure in each runner returned nonzero with failed JUnit XML; native Jenkins recorded pass/fail/unstable fixtures and blocked image build/publication for failed or unstable tests |
| Failure propagation | Isolated child SUCCESS propagated; an intentional child FAILURE caused parent FAILURE |
| Monitoring configuration | Native promtool, amtool and Alloy passed for Compose and six actual Helm variants (three profiles, Slack-only/incident-enabled), including rules and routing |
| Platform audit | 82 retained files inventoried; 17 adapter unit tests, 2 mock-contract tests and 5 real isolated PostgreSQL checks passed |
| Documentation | All eight repositories audited: 42 current Markdown files, 103 Bash examples and 247 local/project links passed |

These tests use native Helm/schema/Jenkins tools and real temporary Git histories.
They do not establish a live production deployment. The intentionally failing
isolated build verifies error propagation; it is not an unresolved test failure.
The locked plugin list includes required transitive dependencies, not 79 optional
features to install separately.

## Target-host acceptance still required

1. Build/test/scan/publish all four services through the laptop's Jenkins agent.
2. Confirm scoped GitHub/GHCR permissions and protection rules. Validate a real
   GitOps PR and ensure the exact boutique/gitops-validation status is required.
3. Merge the four initial dev selections, provision app secrets/data, sync Argo
   and run the full functional smoke suite. Running pods alone do not suffice.
4. Exercise a routine release, same-artifact staging/production promotion and
   rollback with migration compatibility review.
5. Test real Slack delivery, monitoring recovery and database restore; record
   measured SLO coverage and RTO/RPO.

Use [the existing Debian/kind guide](deploy-cicd-kind.md). The user reported the
laptop agent connected before the simplified code was published. Cloud component
checks cannot prove that the updated pins/jobs are installed on that machine.

AWS remains a separate reference. Prior Terraform/component checks do not prove
an AWS apply, CloudWatch/CloudTrail delivery, SNS confirmation or cloud restore.
ServiceNow is optional and needs its own endpoint/credential/live acceptance.
The larger-host [two-cluster exercise](production-lab.md) needs a supported Docker
host, sufficient memory/storage, policy-capable networking and live verification.

[SLOs](slo.md), [recovery targets](recovery-targets.md) and the
[DR runbook](runbooks/disaster-recovery.md) document objectives. Backup scheduling,
off-host storage, HA and regional recovery remain separate operating work until
implemented and measured. GitHub source and same-host Git bundles/ZIPs preserve
source but do not recover database volumes, secrets or Jenkins state.

## Cloud workspace boundary

Use the cloud checkout for source review, component tests, native Helm/schema
checks and isolated Jenkins validation. It is a different machine from the
Debian laptop; cloud commands do not operate that laptop's Jenkins or kind.
The optional two-cluster exercise requires a supported Docker host, writable
cgroups, adequate storage and at least 16 GiB RAM (24–32 recommended). Its doctor
checks prerequisites before creating clusters. The existing 8 GiB laptop follows
the single-cluster deployment guide, with dev first.

The separate-test checks used temporary trusted CA mounts and Maven proxy/DNS
configuration to accommodate this cloud runner. TLS and dependency checksums
remained enabled; those runner settings are outside the repositories. The
Jenkins test-report fixture runs only in a disposable controller, with a local
executor enabled after the production JCasC zero-executor check. Its simulated
build/publication markers do not publish images or run the full delivery flow.

The [platform file audit](file-audit.md) records removed unused copies, optional
components and the local secret helper correction. Its checks did not rerun the
live application journey or deploy a Kubernetes cluster. The existing service
image/build checks above predate this operational cleanup; application code and
Dockerfiles were unchanged by the platform audit.

The seed regression now uses SandboxDslScriptLoader with a real test-user
identity. Earlier job-definition checks used an unsandboxed loader and missed
the deferred XML configure-block failure. The supported DSL replacement passed
the sandboxed check on 2026-10-14; target-host seed execution remains to verify.
