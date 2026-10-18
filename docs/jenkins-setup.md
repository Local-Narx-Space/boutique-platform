# Jenkins setup: one controller, reviewed builds, GitOps PRs

Use the existing controller and connected agent when available. Do not overwrite
its home/security settings by applying fresh-install JCasC. Follow
[the main deployment guide](deploy-cicd-kind.md) for the exact Debian sequence.

## Controller and agents

The controller has zero executors; a Linux inbound agent with label
`trusted-release`, one executor and a private rootless Docker daemon runs reviewed
main builds, seed, promotion, rollback and optional read-only verification.
The Docker workspace must exist at the same absolute path in the daemon host
and agent so bind-mounted integration fixtures work. No rootful host socket is
needed on the controller or build agent. Labels route work, not security.

A separate `terraform-trusted` executor is optional for authorized AWS work. It
needs its own short-lived/scoped cloud identity, not credentials shared with app
builds. Arbitrary PR/fork execution is disabled; add disposable isolated workers
and credential restrictions before enabling untrusted builds.

For a new controller only:

```bash
cd boutique-ci
bash jenkins/scripts/init-local.sh
docker compose -f jenkins/compose.yaml build
docker compose -f jenkins/compose.yaml up -d release-controller
```

The configuration starts one loopback-bound controller and retains release-home.
It is not an upgrade/replacement command for a manually created `jenkins`
container. Back up Jenkins encrypted credentials and home before changes.
Controller/agent artifact verification and plugin locks remain in place.

## Test results plugin

On an existing controller, install **JUnit** through Manage Jenkins → Plugins
and let Jenkins resolve its dependencies. Restart when requested after builds
finish. Do this before updating the shared-library pin: the service pipeline
uses the `junit` step to show individual test results and history. Fresh installs
include the reviewed plugin and dependencies in boutique-ci's checksum lock.

## Shared library and job definitions

Configure the **Global Untrusted Pipeline Library** `boutique-ci`, Modern SCM/Git,
repository `https://github.com/Local-Narx-Space/boutique-ci.git`, default version
set to a reviewed full commit SHA, implicit loading false and overrides false.
Set `BOUTIQUE_CONTROLLER_ROLE=release` in global environment properties.
The variable is a guardrail; it does not isolate code or credentials.

Create a **Pipeline from SCM** seed pinned to that reviewed CI revision with
script path `pipelines/seed-release.Jenkinsfile` and Lightweight checkout off. Keep
`SUPPRESS_AUTOMATIC_BUILDS=true` during setup, then rerun it with false to allow
main builds. It runs `jenkins/jobs/release.groovy` through supported sandboxed DSL,
without custom method approvals. Install/enable Authorize Project separately
for the seed (not preinstalled by the current controller plugin lock). Enable
Per-project configurable Build Authorization under Manage Jenkins → Security →
Access Control for Builds, including Run as a specific user. On the seed job's
Authorization sidebar page, select that strategy and your authorized Jenkins
operator user ID; save before running. A logged-in trigger alone does not select
build identity. Do not disable the sandbox to bypass authorization errors.

The seed generates four app multibranch jobs plus promote/rollback/verify,
manifest validation and infrastructure. `ENABLE_INCIDENT_BRIDGE_BUILD=false`
(default) omits the adapter job; explicitly enable it for ServiceNow. Only main is discovered. Existing
jobs not present in the definition are preserved: disable obsolete bootstrap,
policy and validation jobs manually after checking active runs.

The ServiceNow adapter build is opt-in in the seed with
`ENABLE_INCIDENT_BRIDGE_BUILD=true`. If an earlier seed already created
`boutique-platform`, disable that Jenkins job when the integration is unused;
removedJobAction=IGNORE deliberately preserves existing jobs.

## Credentials

| ID | Kind | Scope/purpose |
| --- | --- | --- |
| github-read | Username/password | Read-only SCM; Contents and Metadata read |
| ghcr-publish | Username/password | Supported package publication token |
| gitops-pr | Username/password | Only GitOps Contents/PR writes; no bypass permission |
| gitops-checks | Secret text | GitOps Contents/PR reads and Commit statuses write |
| kubeconfig-dev/staging/production | Secret file | Optional verifier: read-only deployments/pods and named Argo Application |
| boutique-ca-dev/staging/production | Secret file | Public trusted TLS CA; not a private signing key |

A distinct bot creates GitOps PRs so a human reviewer can approve. Current
username/password bindings support PATs. Adopt a GitHub App only with compatible
short-lived credential handling; do not put an installation ID in a PAT field.
No release-artifact/evidence keys are consumed anymore.
Remove those credentials after migrating pins/jobs and stopping obsolete builds.

For automated verification, configure `BOUTIQUE_DEV_ORIGIN`,
`BOUTIQUE_STAGING_ORIGIN`, and `BOUTIQUE_PRODUCTION_ORIGIN`. HTTPS is required;
only laptop dev accepts exactly `http://localhost:8088`. For a public trust chain,
a public CA bundle can be the file credential. Preserve certificate verification.
Production credentials belong only on approved jobs/workers.

## Delivery and approval

Application main builds test/scan/publish and optionally request dev promotion.
Promotion opens a PR and finishes; it does not poll, merge or deploy. The parent
frees the build executor before calling promotion. Staging/production copy the
preceding environment's image digest/chart without rebuilding. Reviewers confirm
its live smoke/rollout result and database compatibility.

GitOps PRs run **boutique/gitops-validation** through the ordinary
`boutique-gitops-validate` Jenkins job. It reads candidate configuration with
protected-main tools and does not execute candidate scripts. The GitHub status
token is bound only for PR metadata/status API calls, not Helm rendering.
The parent queues validation without waiting, allowing the same single executor
rather than requiring a dedicated policy worker.

Keep `gitops-checks` as Secret text with GitOps Contents/PR read and Commit
statuses write. Require its status, current-base checks, independent review and
CODEOWNERS, stale-review dismissal and no bypass/force pushes. Replace the old
boutique/gitops-policy status. Rerun validation for manual PRs or updated heads.
The job checks configuration, not whether the previous environment passed live
smoke tests; that remains a reviewer responsibility.

On a laptop, use manual/scheduled branch scans; do not expose unauthenticated
Jenkins HTTP to satisfy webhooks. A production webhook endpoint needs TLS,
authentication/signature checks and proper network/access controls.

## Validation

```bash
python3 jenkins/scripts/doctor.py
python3 jenkins/scripts/smoke-controller.py --directory /tmp/boutique-jenkins-check
```

The isolated harness verifies the locked core/plugins, JCasC, actual Job DSL and
Declarative syntax. It exercises failure propagation with no external deploy.
It does not prove GHCR authorization, remote agent provisioning, the complete
application pipeline, Argo sync, notifications or database restore. Verify those
on the target host and retain ordinary build/test/operation reports.

## Restricted verification credentials on the existing host-networked agent

The current boutique-build-agent uses Docker host networking, so the kind API
https://127.0.0.1:41775 is reachable from that agent. Do not use this endpoint for
an agent on another host/network without an appropriate reachable TLS endpoint.
Never disable certificate verification or upload the administrator kubeconfig.

Apply platform/kubernetes/jenkins-verification-rbac.yaml with the administrator.
It defines three separate User identities, boutique:jenkins-verifier:ENV. Each can
get/list/watch Deployments in its own environment namespace and get only its
named Argo Application. It cannot list Secrets, change deployments, synchronize
Argo, or inspect another environment through these grants.

From the platform repository on Debian:

```bash
kubectl --context kind-mega-local apply -f kubernetes/jenkins-verification-rbac.yaml
bash scripts/create-verifier-kubeconfig.sh dev
bash scripts/create-verifier-kubeconfig.sh staging
bash scripts/create-verifier-kubeconfig.sh production
```

The helper is administrator bootstrap, not a service pipeline step. It generates
a private key locally, requests a finite-lived client certificate through the
Kubernetes CSR API, approves only its newly generated request, and embeds that
certificate and the public cluster CA into ~/.kube/boutique-verification/ENV.config.
Directories are mode 700 and output files mode 600. The administrator identity is
not copied. It verifies the issued credential's actual read access before writing
the output and refuses to overwrite an existing credential accidentally.

Upload each output as a Jenkins Secret File credential named kubeconfig-dev,
kubeconfig-staging, or kubeconfig-production. Do not paste its contents into chat,
console logs, Git or environment variables. Give these credentials only to trusted
verification jobs. The certificate requests thirty days; the signer can cap that
lifetime, and the helper prints the actual certificate expiration. Rotate and
replace the Jenkins credential before expiry. Move the existing local file to a
protected backup path before intentional regeneration; existing client certificates
remain valid until expiry. Removing RoleBindings revokes their RBAC grants.

This is a finite-lived certificate bootstrap for this private kind cluster. Cloud
production should prefer the platform's workload identity/OIDC integration or an
automated certificate lifecycle rather than manual credential renewal. Rotation
is not automated by this helper. Review CSR approvals and retain the public CSR
objects/API audit trail; Jenkins gets no CSR creation or approval rights.
