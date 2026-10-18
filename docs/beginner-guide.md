# Detailed beginner guide to Boutique

The project is eight independent repositories with a small four-service app and
production-style CI/CD. This edition simplifies delivery: one Jenkins controller,
reviewed builds, image digests, protected GitOps PRs, Helm and Argo CD. Custom
signed releases, deployment evidence, policy executor and aggregate bootstrap
jobs have been removed.

**Deploy on your existing Debian/kind laptop:** follow
[deploy-cicd-kind.md](deploy-cicd-kind.md) in order. It starts from your existing
cluster and connected agent, uses GHCR images and app port 8088, and keeps
Jenkins on port 8080. Do not mix its registry profile with the optional local-image
exercise below. You do not need to manually deploy the app before CI/CD.

## 1. How to read this guide

Sections 2–5 explain the architecture and files. Sections 6–8 describe machine,
cloning and optional Compose. Sections 9–10 are optional manual Kubernetes
practice. Section 11 explains Helm. Current delivery, migration, environment
separation and verification are in the main deployment guide linked above.
Monitoring, AWS and recovery are retained as operating topics later in this file.
Production is an operating environment with measured controls, not a tool count.

## 2. Understand the whole system

Think of five connected layers:

1. **Application:** the storefront, product API, cart API and orders API.
2. **Data:** Redis for carts and PostgreSQL for durable orders.
3. **Runtime:** Docker Compose, or Kubernetes with networking, TLS and storage.
4. **Delivery:** Jenkins builds artifacts; GitOps records the selected release;
   Argo CD reconciles that release into Kubernetes.
5. **Operations:** metrics, logs, alerts, incidents, verification and recovery.

~~~mermaid
flowchart LR
    Browser["Your browser"] --> Frontend["Frontend / API gateway"]
    Frontend --> Catalogue["Catalogue: Go"]
    Frontend --> Cart["Cart: Python"]
    Frontend --> Orders["Orders: Java"]
    Cart --> Redis["Redis"]
    Orders --> Postgres["PostgreSQL"]
    Orders --> Catalogue
    Orders --> Cart
    Apps["Application metrics"] --> Prometheus["Prometheus"]
    Prometheus --> Grafana["Grafana"]
    Prometheus --> Alertmanager["Alertmanager"]
    Alertmanager --> Slack["Slack or local mock"]
    Alertmanager --> Bridge["Incident adapter"]
    Bridge --> Queue["Dedicated incident queue"]
    Bridge --> SN["ServiceNow or local mock"]
    Logs["Container logs"] --> Alloy["Grafana Alloy"]
    Alloy --> Loki["Loki"]
    Loki --> Grafana
~~~

The catalogue, cart and orders services are backend services. They do not need
public browser ports. The frontend is the browser entry point and forwards
requests to the appropriate internal service.

Jenkins and Argo CD are management tools. A customer request does not travel
through Jenkins, Terraform or Argo CD. Those tools decide and maintain which
application release is running.

### Who changes what?

| Actor | Responsibility |
| --- | --- |
| You, as a developer | Change application source and service-owned Helm templates through review |
| Jenkins build job | Test/build/scan; publish the image and chart; archive checksums and scan reports |
| Jenkins delivery job | Validate releases and prepare a GitOps pull request |
| GitHub checks/review | Enforce policy and approval before the configuration merges |
| Argo CD | Read merged GitOps configuration and reconcile Kubernetes resources |
| Kubernetes | Schedule Pods, restart failed containers and route traffic through Services |
| Trusted verifier | Check the actual deployment and HTTP behavior; archive rollout/smoke reports |
| You, as an operator | Configure identities, bootstrap foundations, investigate alerts and exercise recovery |

This division explains why there are many files. A Dockerfile answers “how is
the app packaged?” A Helm template answers “how should Kubernetes run it?”
A pipeline answers “when is a release allowed?” A runbook answers “what do I do
when it fails?” They serve different purposes.

## 3. Understand the application request flow

### 3.1 Browse products

1. Your browser loads the frontend page.
2. Its JavaScript requests the frontend endpoint **GET /api/products**.
3. The frontend calls the internal catalogue endpoint **GET /products**.
4. The catalogue returns the sample product list.

The catalogue is intentionally simple and has no separate catalogue database.
You can concentrate on deployment rather than implementing inventory management.

### 3.2 Add an item to the cart

1. The frontend issues a signed anonymous-session cookie.
2. The browser sends **PUT /api/cart/items/mug** with a quantity.
3. The frontend verifies the session and the mutation request's Origin.
4. It calls the cart service using the session identity it derived itself.
5. The cart service stores the session's cart in Redis.

The frontend does not trust an arbitrary session header supplied by a browser.
The cookie is HttpOnly and SameSite=Strict. HTTPS profiles enable Secure.
Changing the session signing key invalidates existing sessions; this is why
secret preservation and backup matter.

### 3.3 Checkout

1. The browser sends **POST /api/orders** with an idempotency key.
2. The frontend forwards the request with the verified session identity.
3. Orders reads the cart and catalogue data.
4. It computes the amount on the server, using integer minor currency units.
5. PostgreSQL stores the order and its line-item snapshot in a transaction.

An **idempotency key** identifies one checkout attempt. Repeating that attempt,
including concurrent retries, returns the same order instead of creating
several orders. The implementation combines PostgreSQL advisory locks with a
unique session/key constraint.

Checkout retains the cart. A different checkout key can create another order.
The app has no actual payment processor, stock reservation, account login or
email service. Orders belong to anonymous sessions, so it is a learning
application rather than a finished commercial storefront.

### 3.4 Ports and dependencies

| Component | Container/service port | Main dependency |
| --- | --- | --- |
| Frontend | 8080 | Catalogue, cart, orders and a session signing secret |
| Catalogue | 8081 | Its packaged product data |
| Cart | 8082 | Redis credentials/connectivity |
| Orders | 8083 | PostgreSQL, catalogue and cart |
| Incident adapter | 8084 | SQLite or its separate PostgreSQL queue and receiver credentials |
| PostgreSQL | 5432 | Persistent storage and matching database credentials |
| Redis | 6379 | Persistent storage and matching Redis credentials |

Application endpoints include **/health/live**, **/health/ready** and metrics
endpoints. Liveness asks whether the process should be restarted. Readiness asks
whether it can currently serve traffic. A running Java process with a broken
database connection is not necessarily ready.

## 4. Understand the eight repositories

Your local folder contains eight independent Git repositories. Each has its own
history, main branch, origin and access rules. A parent workspace folder or VS
Code workspace does not turn them into a monorepo.

| Repository | Main question it answers | Read this first |
| --- | --- | --- |
| [boutique-frontend](https://github.com/Local-Narx-Space/boutique-frontend) | How does the browser reach the app safely? | src/server.js, src/session.js, Dockerfile |
| [boutique-catalogue](https://github.com/Local-Narx-Space/boutique-catalogue) | Where do products and prices come from? | main.go, metrics.go, Dockerfile |
| [boutique-cart](https://github.com/Local-Narx-Space/boutique-cart) | How are per-session carts stored? | app.py, Dockerfile |
| [boutique-orders](https://github.com/Local-Narx-Space/boutique-orders) | How does checkout create durable orders? | OrdersController.java and the Flyway migration |
| [boutique-ci](https://github.com/Local-Narx-Space/boutique-ci) | How do we test, publish and authorize delivery? | vars/servicePipeline.groovy and pipelines/ |
| [boutique-infrastructure](https://github.com/Local-Narx-Space/boutique-infrastructure) | What AWS resources would support this system? | README.md, environments/ and modules/platform/ |
| [boutique-gitops](https://github.com/Local-Narx-Space/boutique-gitops) | What release/configuration should each environment run? | environments/dev/ and docs/helm-delivery.md |
| [boutique-platform](https://github.com/Local-Narx-Space/boutique-platform) | How do we assemble, verify and operate everything? | This guide, scripts/ and local/ |

### 4.1 Frontend repository

~~~text
boutique-frontend/
  src/server.js          HTTP entry point and internal API forwarding
  src/session.js         Signed anonymous-session handling
  src/metrics.js         HTTP/process metrics
  public/                HTML, CSS and browser JavaScript
  tests/                 Session and metrics tests
  package.json           Node commands and project metadata
  package-lock.json      Resolved dependency lock
  Dockerfile             Test environment target and non-root runtime image
  Jenkinsfile            Calls the common service pipeline
  helm/                  Kubernetes chart owned by this service
~~~

The frontend uses Node.js 22 in its image. The browser assets are ordinary
HTML/CSS/JavaScript; this project does not require learning a large frontend
framework.

Read the frontend URL settings when diagnosing a failure: internal service
URLs are Kubernetes/Compose addresses, while **PUBLIC_ORIGIN** is the address
the browser uses. A wrong public origin can reject a cart mutation even when
every Pod is healthy.

### 4.2 Catalogue repository

~~~text
boutique-catalogue/
  main.go                Product HTTP API and health behavior
  metrics.go             Request metrics
  main_test.go           Go tests
  go.mod                 Go module metadata
  Dockerfile             Test environment, compilation and small runtime
  Jenkinsfile            Common pipeline with service=catalogue
  helm/                  Deployment, Service, account and optional PDB
~~~

Go compiles the service into a binary. A multistage Docker build leaves the
compiler and test tooling out of the final runtime. The catalogue is useful for
studying a relatively stateless microservice.

### 4.3 Cart repository

~~~text
boutique-cart/
  app.py                 FastAPI routes, validation and Redis client
  tests/test_validation.py
  requirements.in        Human-maintained runtime dependency input
  requirements.txt       Hash-locked runtime dependencies
  requirements-dev.in    Test dependency input
  requirements-dev.txt   Hash-locked test dependencies
  Dockerfile             Dependency/test-environment/runtime stages
  Jenkinsfile
  helm/
~~~

The service uses Python/FastAPI and Redis. The hash-locked requirement files are
important: installing different dependency versions in two environments can
change behavior even if your application source is unchanged.

The Dockerfile provides an isolated `test` target with the locked test dependencies.
Jenkins runs that target as a container in its separate Test stage, then records
JUnit results. A runtime image build does not execute tests. You do not need to
install FastAPI into Debian’s system Python simply to deploy the image.

### 4.4 Orders repository

~~~text
boutique-orders/
  pom.xml                Maven dependencies and build
  src/main/java/dev/boutique/orders/
    Application.java     Spring Boot entry point
    OrdersController.java
  src/main/resources/
    application.properties
    db/migration/V1__orders.sql
  src/test/              Java validation tests
  certs/                 Public AWS RDS CA bundle and checksum
  scripts/fetch-rds-ca.sh
  docs/security-exceptions.md
  Dockerfile             Maven test environment, packaging and Java 21 runtime
  Jenkinsfile
  helm/
~~~

Flyway applies versioned database migrations. The first migration creates the
order schema. Schema changes and image changes must be coordinated: rolling back
an image does not automatically reverse a database migration.

The CA bundle is public trust material, not an AWS credential. It lets the cloud
orders client verify its RDS server. Lab database TLS uses the separately
generated lab CA.

The security exception document describes a scoped, expiring image-scan
exception. An exception is an item to review; it is not a reason to ignore all
scanner findings.

### 4.5 CI repository

~~~text
boutique-ci/
  vars/servicePipeline.groovy    Shared application quality/publication stages
  vars/releaseArtifact.groovy    Retrieve dev publishing artifacts
  vars/gitopsPullRequest.groovy  Open a reviewable deployment PR
  pipelines/seed-release.Jenkinsfile  Pipeline-based job creation
  pipelines/deploy-dev.Jenkinsfile     Dev promotion and verification entry point
  pipelines/deploy-qa.Jenkinsfile      Staging promotion and verification entry point
  pipelines/deploy-prod.Jenkinsfile    Production promotion and verification entry point
  pipelines/rollback.Jenkinsfile       Restore one service from Git history
  pipelines/production-verify.Jenkinsfile  Guard production PR verification
  pipelines/gitops-validate.Jenkinsfile    Validate exact GitOps PR head
  jenkins/jobs/release.groovy    Main-only Job DSL definitions
  jenkins/compose.yaml           Optional fresh single-controller installation
  jenkins/casc/jenkins.yaml      Declarative controller configuration
  jenkins/agents/                Common inbound build agent
  jenkins/scripts/               Verified installers and real controller checks
~~~

A service's Jenkinsfile calls the shared library rather than copying every
stage. The library is pinned to a reviewed full commit. Job DSL creates job
configuration; Pipeline DSL runs builds. This distinction does not imply two
programming languages: both use Groovy syntax.

### 4.6 Infrastructure repository

~~~text
boutique-infrastructure/
  bootstrap/                Protected AWS Terraform state storage
  environments/
    nonprod/                Nonprod VPC/EKS; dev and staging data
    production/             Separate production VPC/EKS/data
    audit/                  Once-per-account CloudTrail/security logging
  modules/platform/
    main.tf                 Shared AZ selection and tags
    networking.tf           VPC, subnet/NAT settings and data access rules
    eks.tf                  Private cluster, nodes, access and core addons
    iam.tf                  Cluster/node/OIDC/External Secrets identities
    encryption.tf           Platform KMS key
    databases.tf            RDS and Redis
    secrets.tf              Application credential creation/delivery
    budgets.tf              Monthly cost notification
    monitoring.tf           Dedicated queue DB and integration identities
    observability.tf        CloudWatch logs, alarms and optional telemetry
    operator.tf             Private SSM-only administrative host
    variables.tf            Input contract
    outputs.tf              Output contract
    versions.tf             Terraform/provider requirements
  scripts/                  Preflight, database setup, sparse plan summary
  Jenkinsfile               Trusted Terraform plan/approval/apply job
~~~

Every .tf file in modules/platform belongs to the same module. Separate files
improve navigation. Separate environment roots and state keys provide the
nonprod/production state boundaries.

You do not apply this repository to deploy on kind. Kind provides local
Kubernetes nodes using Docker, not AWS VPCs or EKS.

### 4.7 GitOps repository

~~~text
boutique-gitops/
  environments/dev/
    Chart.yaml              Umbrella chart and exact app chart versions
    charts/*.tgz            Verified, committed service chart packages
    values.yaml             Environment configuration
    releases.yaml           Selected image identities
    templates/              Namespace, quotas and network policy
  environments/staging/     Same structure with staging settings
  environments/production/  Same structure with production settings
  homelab/<env>/values.yaml  Local-image, introductory kind profile
  lab-profiles/<env>/        TLS/local-data production-learning additions
  applications/             Cloud/default Argo Application examples
  monitoring/               Shared monitoring Helm chart and profiles
  platform/                 Ingress, issuers and AWS secret integration
  dependencies/homelab/      Optional separate local-data chart
  .github/CODEOWNERS         Review ownership for deployment/configuration
  laptop-profiles/dev/       Existing-kind registry deployment
  scripts/                  Render, validate, promote and recovery tools
~~~

This repository answers “which image and which chart should dev run?” It does
not contain the app's normal business logic. A catalogue source change belongs
in the catalogue repository; a change to the production replica count belongs
in GitOps configuration.

### 4.8 Platform repository

~~~text
boutique-platform/
  local/compose.yaml        Fast six-component application runtime
  local/kind.yaml           Small introductory cluster configuration
  local/production-lab/
    kind-nonprod.yaml       Three-node nonprod cluster
    kind-production.yaml    Three-node production cluster
    versions.lock.json     Verified CLI/controllers/node-image pins
    .state/                Generated private state; ignored
    .cache/                Verified downloaded artifacts; ignored
  scripts/
    init-local.sh          Preserve/generate local app credentials
    local-up.sh            Build/start Compose and run smoke checks
    k8s-secrets.py         Generate/preserve introductory Kubernetes Secrets
    production-lab.py     Full lab foundation, secrets, delivery and drills
    wait-for-deployment.py Trusted verification helper
    backup-local.sh       Local PostgreSQL backup
    restore-drill.sh      Restore into a disposable database
  tests/smoke/             Real HTTP application checks
  tests/config/            Delivery/rendering/policy tests
  monitoring/              Compose monitoring, rules, dashboard, adapter
  docs/                    Architecture, operations and recovery guides
  workspace/               Eight-repository editor template
  Jenkinsfile              Builds the incident adapter as a deployable service
~~~

The incident adapter is the fifth custom deployable workload. Redis,
PostgreSQL, Grafana and Argo are additional software components, but they are not
custom business services with their own application repositories.

The current deployment guide explicitly selects your existing cluster. The
optional manual exercise uses an isolated operator kubeconfig.

## 5. Understand the recurring file types

| File/type | What to understand | Typical edit |
| --- | --- | --- |
| Dockerfile | Build/test environments, final runtime, user and entry point | Update an app runtime/dependency through review |
| .dockerignore | What is excluded from Docker build context | Prevent secrets/Git/cache files entering the build |
| Jenkinsfile | Which job/library runs for this repository | Keep service entrypoints thin |
| helm/Chart.yaml | Chart name and version metadata | Versioned service chart packaging |
| helm/templates/ | Kubernetes object definitions | Change a service's runtime resources |
| values.yaml | Inputs to templates | Set environment behavior and resources |
| values.schema.json | Allowed value shapes/types | Reject invalid configuration early |
| releases.yaml | Selected release images | Delivery jobs update immutable digests |
| charts/*.tgz | Packaged chart templates and defaults | Verified release packages committed by delivery |
| .terraform.lock.hcl | Provider selections/checksums | Reviewed dependency upgrade |
| CODEOWNERS | Which paths require owner review | Protect policy/pipeline/production changes |
| .gitignore | Generated/private/cache exclusions | Keep local state out of commits |
| README/runbook | Intent, setup and response procedure | Record how operators actually use the system |

Generated files are important but are not all source code. For example, deleting
an ignored session-key file can break session continuity even though Git reports
a clean working tree. An ignored Terraform state can contain sensitive values.
Treat Git status as a source-control check, not a full backup inventory.

## 6. Prepare your Debian laptop

### 6.1 Confirm the host

Use a normal Debian host or supported Linux VM. The pinned CLI installers are
for Linux amd64. Check:

~~~bash
cat /etc/os-release
uname -m
free -h
df -h
~~~

The expected architecture is x86_64. An ARM laptop requires separately reviewed
tool/image compatibility; the production lab rejects unsupported CLI
architecture rather than downloading an arbitrary binary.

Do not run the two-cluster lab inside an ordinary unprivileged application
container. Kind nodes need the host's container/kernel/cgroup capabilities.

### 6.2 Install basic tools

~~~bash
sudo apt-get update
sudo apt-get install -y \
  ca-certificates curl gnupg git openssl unzip jq make \
  python3 python3-venv python3-yaml python3-jsonschema
~~~

Debian supplies Python/YAML tools for the platform scripts. The app's Node,
Go, Python and Java runtime dependencies come from its Docker builds.
Avoid using sudo pip to modify Debian's system Python.

### 6.3 Install Docker Engine and Compose

If Docker and the Compose plugin already work, keep that installation and skip
the installation block. Check:

~~~bash
docker version
docker compose version
docker info
~~~

For a fresh Debian installation, use Docker's official Debian repository.
The commands below configure Docker's signed apt source:

~~~bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

(
  . /etc/os-release
  printf 'Types: deb\nURIs: https://download.docker.com/linux/debian\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/docker.asc\n' \
    "$VERSION_CODENAME" "$(dpkg --print-architecture)"
) | sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null

sudo apt-get update
sudo apt-get install -y \
  docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
~~~

Check Docker's [current Debian installation instructions](https://docs.docker.com/engine/install/debian/)
if your distribution codename is unsupported or conflicting older Docker
packages are installed. Do not purge your existing installation/data to make
these examples run.

If your normal operator account needs access to the laptop Docker daemon:

~~~bash
sudo usermod -aG docker "$USER"
~~~

Log out and log back in before continuing. Docker-group membership grants
powerful control over this host. It is suitable for your trusted local operator,
but it does not isolate an untrusted PR build. Build-agent isolation comes later.

~~~bash
docker run --rm hello-world
docker info --format '{{.Driver}}'
docker compose version
~~~

The hello-world container only checks Docker. It does not verify the app or
Kubernetes. The production-lab doctor performs additional capacity checks.

### 6.4 Install the project CLIs

Do this after cloning the workspace in section 7:

~~~bash
cd "$BOUTIQUE_ROOT/boutique-platform"
./scripts/install-tools.sh "$PWD/.tools"
export PATH="$BOUTIQUE_ROOT/boutique-platform/.tools:$PATH"
kubectl version --client=true
kind version
helm version --short
terraform version
kubeconform -v
~~~

The installer verifies published/reviewed checksums. It installs kubectl 1.34.0,
Helm 3.22.0, Terraform 1.11.4, kubeconform 0.6.7 and kind 0.30.0.
The full lab separately downloads its locked kind 0.33.0 binary. Its helper
prefers that cached binary; seeing kind 0.30.0 on your shell PATH does not mean
the full lab uses it.

These are project pins, not a promise that every pin is the newest or remains
secure indefinitely. Update versions as a reviewed dependency change.

### Checkpoint A

You can run Docker as the intended operator, Compose is available, the host
architecture is supported, and Python can import its manifest dependencies:

~~~bash
python3 -c 'import yaml, jsonschema; print("Python prerequisites ready")'
~~~

## 7. Clone the workspace

Choose one parent directory. The Compose file and scripts expect sibling
repositories at these exact directory names:

~~~text
$HOME/devops-boutique/
  boutique-frontend/
  boutique-catalogue/
  boutique-cart/
  boutique-orders/
  boutique-ci/
  boutique-infrastructure/
  boutique-gitops/
  boutique-platform/
~~~

Run:

~~~bash
export BOUTIQUE_ROOT="$HOME/devops-boutique"
mkdir -p "$BOUTIQUE_ROOT"
cd "$BOUTIQUE_ROOT"

for repo in frontend catalogue cart orders ci infrastructure gitops platform; do
  if [ -e "boutique-$repo" ]; then
    printf 'Preserving existing directory: boutique-%s\n' "$repo"
  else
    git clone "https://github.com/Local-Narx-Space/boutique-$repo.git"
  fi
done
~~~

If an existing directory is not the intended checkout, inspect it rather than
overwriting it. Verify all eight:

~~~bash
for repo in frontend catalogue cart orders ci infrastructure gitops platform; do
  git -C "$BOUTIQUE_ROOT/boutique-$repo" status --short
  git -C "$BOUTIQUE_ROOT/boutique-$repo" remote get-url origin
done
~~~

A new terminal will not remember your exported variables automatically.
Re-enter BOUTIQUE_ROOT and PATH when opening another terminal for this guide.
Keep credentials out of shell startup files.

You can open workspace/boutique.code-workspace from the platform repository in
VS Code after verifying its relative folders resolve in your chosen layout.

Use normal Git clone/pull commands for the eight published repositories.
Repository creation/publication helpers have been retired.

### Checkpoint B

All eight checkouts exist beside one another. You understand that changing the
frontend and GitOps creates changes in two separate Git repositories.

## 8. Run the application with Docker Compose

**Optional application exercise.** The main CI/CD-first workflow does not
require this Compose deployment. Continue with its cluster foundations and
Jenkins setup using [the ordered main guide](deploy-cicd-kind.md).

### 8.1 Start it

~~~bash
cd "$BOUTIQUE_ROOT/boutique-platform"
./scripts/local-up.sh
~~~

The script:

1. Preserves existing local/.env credentials or generates missing values.
2. Builds the four application images.
3. Starts six components from local/compose.yaml.
4. Runs the real HTTP smoke suite.

Docker pulls dependencies/runtime images and builds images. The first Java and
Python builds can take time. Follow the build output; do not assume a long
dependency download means the system is broken.

### 8.2 Open and inspect

Open **http://localhost:8080** in your laptop browser.

~~~bash
cd "$BOUTIQUE_ROOT/boutique-platform"
docker compose --env-file local/.env -f local/compose.yaml ps
docker compose --env-file local/.env -f local/compose.yaml logs --tail=100 frontend
docker compose --env-file local/.env -f local/compose.yaml logs --tail=100 orders
curl -fsS http://localhost:8080/health/ready
curl -fsS http://localhost:8080/api/products | python3 -m json.tool
~~~

Only the frontend is bound to a laptop application port. Internal names such as
catalogue and postgres are Docker network DNS names.

The Compose project is named boutique. Its generated app images are
boutique-frontend:latest, boutique-catalogue:latest, boutique-cart:latest and
boutique-orders:latest. These local names are not the registry release identities
used by the full delivery track.

### 8.3 Understand the smoke test

~~~bash
python3 tests/smoke/smoke.py \
  --environment dev \
  --report /tmp/boutique-compose-smoke.json
~~~

The suite checks browsing, cart behavior, invalid input, Origin enforcement,
session isolation, server pricing, checkout, repeat/concurrent idempotency,
order ownership, empty checkout and item deletion.

This is stronger than “the container started.” It exercises HTTP behavior
across the application and databases. It also creates synthetic orders, so run
it against this project's learning data rather than unrelated customer data.

### 8.4 Understand persistence and credentials

Redis and PostgreSQL use named Docker volumes. Restarting their containers does
not erase those volumes. local/.env contains the matching generated credentials.

Do not delete local/.env while retaining initialized database volumes. A new
password in an environment file does not update a database's existing password.
Changing credentials is a coordinated rotation operation.

### Checkpoint C

You can use the storefront, the smoke report succeeds, and you can explain which
service talks to which database. Continue to kind only after this works.

## 9. Deploy the application in your kind cluster

**Optional manual Kubernetes exercise.** This is not the first application
deployment step in the main CI/CD workflow. That workflow publishes tested
releases first and lets Argo deploy the reviewed GitOps baseline.

This is the beginner exercise. It deliberately uses locally built images and
directly applied Helm-rendered manifests. It does not establish production
release publication, Argo Applications, TLS ingress or the two-cluster boundary.

### 9.1 Select an existing cluster or create a dedicated one

List existing clusters:

~~~bash
kind get clusters
kubectl config get-contexts
~~~

If you already have a suitable kind cluster, set its actual kind name:

~~~bash
export BOUTIQUE_KIND_CLUSTER="YOUR_EXISTING_KIND_NAME"
export BOUTIQUE_KIND_CONTEXT="kind-$BOUTIQUE_KIND_CLUSTER"
~~~

Replace YOUR_EXISTING_KIND_NAME; do not copy it literally. Verify the intended
cluster has enough capacity and a working dynamic StorageClass:

~~~bash
kubectl --context "$BOUTIQUE_KIND_CONTEXT" get nodes -o wide
kubectl --context "$BOUTIQUE_KIND_CONTEXT" get storageclass
~~~

The manifests and kubectl pin target Kubernetes 1.34. Check version compatibility
before using an older/newer cluster. An existing namespace called boutique-dev
must belong to this exercise; do not overwrite someone else's workload.

If you want a fresh dedicated introductory cluster instead:

~~~bash
export BOUTIQUE_KIND_CLUSTER="boutique-learning"
export BOUTIQUE_KIND_CONTEXT="kind-$BOUTIQUE_KIND_CLUSTER"
kind create cluster \
  --name "$BOUTIQUE_KIND_CLUSTER" \
  --config "$BOUTIQUE_ROOT/boutique-platform/local/kind.yaml" \
  --image kindest/node:v1.34.0
~~~

Run the create command only when that cluster name does not exist. This small
configuration has one control plane and one worker. The stricter two-cluster
lab later uses a reviewed node-image digest and different controller networking.

### 9.2 Isolate the operator kubeconfig

The introductory secret helper invokes kubectl without a context flag. Give this
terminal an operator kubeconfig that contains only your chosen kind cluster:

~~~bash
export BOUTIQUE_OPERATOR_DIR="$HOME/.local/share/boutique-learning"
install -d -m 0700 "$BOUTIQUE_OPERATOR_DIR"
umask 077
kind get kubeconfig --name "$BOUTIQUE_KIND_CLUSTER" \
  > "$BOUTIQUE_OPERATOR_DIR/kind-operator.yaml"
chmod 0600 "$BOUTIQUE_OPERATOR_DIR/kind-operator.yaml"
export KUBECONFIG="$BOUTIQUE_OPERATOR_DIR/kind-operator.yaml"
kubectl config current-context
kubectl get nodes
~~~

Confirm the printed context is the cluster you selected. This file grants
operator access and stays outside Git. It is not a Jenkins verifier credential.

### 9.3 Build and load the images

~~~bash
cd "$BOUTIQUE_ROOT/boutique-platform"
./scripts/init-local.sh
docker compose --env-file local/.env -f local/compose.yaml build

for service in frontend catalogue cart orders; do
  docker tag "boutique-$service:latest" "boutique-$service:local"
  kind load docker-image "boutique-$service:local" \
    --name "$BOUTIQUE_KIND_CLUSTER"
done
~~~

Loading images copies them into the kind nodes' container runtime. An image
existing in the laptop Docker daemon is not automatically available to
Kubernetes nodes.

### 9.4 Create the local Kubernetes credentials

~~~bash
cd "$BOUTIQUE_ROOT/boutique-platform"
python3 scripts/k8s-secrets.py dev
kubectl --context "$BOUTIQUE_KIND_CONTEXT" -n boutique-dev get secrets
~~~

The helper creates/preserves redis-auth, cart-redis, frontend-session and
orders-database. It refuses a partial Redis credential pair. This introductory
profile uses local data services without the full lab's database TLS.

Listing Secret names is useful. Dumping their values into a screenshot, commit
or chat is not needed to verify creation.

### 9.5 Render, inspect, then apply

~~~bash
python3 "$BOUTIQUE_ROOT/boutique-gitops/scripts/render.py" dev \
  --profile local > /tmp/boutique-dev-local.yaml

kubeconform -strict -summary \
  -kubernetes-version 1.34.0 /tmp/boutique-dev-local.yaml

kubectl --context "$BOUTIQUE_KIND_CONTEXT" \
  apply -f /tmp/boutique-dev-local.yaml
~~~

The render command combines the environment umbrella chart and its committed
service packages with the local values profile. It includes the local databases.
Do not also install dependencies/homelab into this namespace: that optional
chart can duplicate the data resources.

The temporary YAML contains references to Kubernetes Secrets; it does not need
their generated values. You can inspect its Deployments, Services, StatefulSets,
quota and NetworkPolicy objects before applying it.

### 9.6 Wait for the app

~~~bash
for service in frontend catalogue cart orders; do
  kubectl --context "$BOUTIQUE_KIND_CONTEXT" -n boutique-dev \
    rollout status "deployment/$service" --timeout=300s
done

kubectl --context "$BOUTIQUE_KIND_CONTEXT" -n boutique-dev get pods -o wide
kubectl --context "$BOUTIQUE_KIND_CONTEXT" -n boutique-dev get services
kubectl --context "$BOUTIQUE_KIND_CONTEXT" -n boutique-dev get pvc
~~~

PostgreSQL/Redis must start and bind their storage before dependent applications
become ready. A rollout timeout means inspect the failing Pod/events rather than
repeatedly applying the same file.

### 9.7 Open the app through port-forward

The local profile expects the browser Origin **http://localhost:8080**.
If the Compose app is still using port 8080, stop that app stack first:

~~~bash
cd "$BOUTIQUE_ROOT/boutique-platform"
docker compose --env-file local/.env -f local/compose.yaml down
~~~

This preserves volumes. If you started the combined Compose monitoring stack,
use its combined-file stop command in section 22 instead.

In terminal A:

~~~bash
kubectl --context "$BOUTIQUE_KIND_CONTEXT" -n boutique-dev \
  port-forward --address 127.0.0.1 service/frontend 8080:8080
~~~

Keep that terminal open. Open **http://localhost:8080** in your browser.
In terminal B, restore BOUTIQUE_ROOT and run:

~~~bash
cd "$BOUTIQUE_ROOT/boutique-platform"
BASE_URL=http://localhost:8080 python3 tests/smoke/smoke.py \
  --environment dev --report /tmp/boutique-kind-smoke.json
~~~

Do not change the exposed port/origin independently of PUBLIC_ORIGIN. For
example, a different browser port can make mutations fail their Origin check.

### 9.8 Know this exercise's boundaries

The default kind network does not demonstrate the Calico policy enforcement used
in the full lab. Having a NetworkPolicy object is insufficient; your CNI must
implement it. An existing cluster with a policy-capable CNI can enforce policy,
but you still need an executable connectivity test.

Direct kubectl apply is a useful introductory exercise. Once Argo owns the full
lab, use the reviewed GitOps delivery workflow instead.

### Checkpoint D

All four application Deployments are ready, database PVCs are Bound, and the
HTTP smoke suite succeeds through the port-forward. You can locate the rendered
image selection and understand why loading images into kind was necessary.

## 10. Inspect and understand the Kubernetes deployment

### 10.1 Deployment, Pod and container

A Deployment specifies the desired app replicas and Pod template. A ReplicaSet
maintains those replicas. A Pod contains the app container, its environment
configuration and mounts. The container image contains the executable code.

~~~bash
kubectl --context "$BOUTIQUE_KIND_CONTEXT" -n boutique-dev get deployments
kubectl --context "$BOUTIQUE_KIND_CONTEXT" -n boutique-dev get replicasets
kubectl --context "$BOUTIQUE_KIND_CONTEXT" -n boutique-dev get pods
kubectl --context "$BOUTIQUE_KIND_CONTEXT" -n boutique-dev \
  describe deployment/frontend
~~~

Read the image, resources, probes and Secret references. These are different
from the application source files that produced the image.

### 10.2 Service and DNS

A Service gives changing Pods a stable name/address. In this namespace, the
frontend can call http://catalogue:8081 even after a catalogue Pod is replaced.
Services select Pods by labels, so a label mismatch can leave a Service with no
ready endpoints.

~~~bash
kubectl --context "$BOUTIQUE_KIND_CONTEXT" -n boutique-dev get endpointslices
kubectl --context "$BOUTIQUE_KIND_CONTEXT" -n boutique-dev describe service/cart
~~~

### 10.3 StatefulSet and PVC

A StatefulSet suits a local data workload that needs stable identity/storage.
A PersistentVolumeClaim requests storage from the cluster's provisioner.
A Bound PVC does not establish that the data has an off-host backup.

~~~bash
kubectl --context "$BOUTIQUE_KIND_CONTEXT" -n boutique-dev get statefulsets
kubectl --context "$BOUTIQUE_KIND_CONTEXT" -n boutique-dev describe pvc
kubectl --context "$BOUTIQUE_KIND_CONTEXT" get storageclass
~~~

Kind's local storage remains on its Docker-node disks. Deleting the cluster can
lose those disks and the databases. Do not confuse a persistent Pod volume with
surviving laptop/cluster deletion.

### 10.4 Resources and security settings

Requests influence scheduling. Limits constrain resource use. Java's memory
settings and Pod memory limit must agree; an OOMKilled event is a memory problem,
not necessarily an application exception.

Non-root users, dropped capabilities, read-only root filesystems and restricted
Pod settings reduce avoidable runtime privileges. A writable /tmp mount permits
necessary temporary data while retaining the read-only application filesystem.

### 10.5 Events and logs

~~~bash
kubectl --context "$BOUTIQUE_KIND_CONTEXT" -n boutique-dev \
  get events --sort-by=.lastTimestamp
kubectl --context "$BOUTIQUE_KIND_CONTEXT" -n boutique-dev \
  logs deployment/orders --tail=100
~~~

For a repeatedly crashing Pod, copy its actual name from get pods:

~~~bash
kubectl --context "$BOUTIQUE_KIND_CONTEXT" -n boutique-dev \
  logs YOUR_POD_NAME --previous --tail=100
~~~

The previous flag retrieves the last terminated container's logs when available.
Replace YOUR_POD_NAME; it is not a resource included in the project.

## 11. Understand Helm and the environment profiles

Helm is a template/package tool for Kubernetes configuration. A chart combines
templates, defaults and metadata. Values customize its rendering.

In this project, a service owns its chart in its application repository.
The GitOps environment uses an umbrella chart with four service dependencies.
The service packages are committed under charts/ after verification.

~~~mermaid
flowchart LR
    Source["Service repo: code + helm"] --> Jenkins["Tested release build"]
    Jenkins --> Image["GHCR image digest"]
    Jenkins --> Chart["Versioned chart package and checksum"]
    Chart --> Git["GitOps: charts/*.tgz"]
    Image --> Values["GitOps: releases.yaml"]
    Git --> Render["Helm rendering"]
    Values --> Render
    Config["Environment/profile values"] --> Render
    Render --> Argo["Argo CD reconciliation"]
~~~

### Values merge order

| Profile | Value files added to the environment chart | Intended runtime |
| --- | --- | --- |
| cloud | values.yaml, releases.yaml | Managed cloud services |
| laptop | values.yaml, releases.yaml, laptop-profiles/dev/values.yaml | Existing Debian/kind dev with GHCR image digests |
| lab | values.yaml, releases.yaml, lab-profiles/<env>/values.yaml | Two-cluster TLS kind lab |
| local | values.yaml, releases.yaml, homelab/<env>/values.yaml | Introductory locally loaded images/data |

Later values override earlier values where Helm's merge rules permit.
Lists can be replaced, so inspect the rendered YAML rather than assuming every
list is appended.

The checked-in baseline charts use **0.1.0-bootstrap** and deliberately unavailable
bootstrap images. The local profile overrides those image names for the beginner
exercise. The full lab refuses bootstrap/local tags; it needs real releases.

Changing service helm/templates changes the next packaged chart release.
Changing a local application checkout does not change a previously vendored
chart package in GitOps. Build/publish/promote the new chart through the pipeline.

Argo uses Helm to render manifests and then manages them itself. You will not
necessarily see these applications as ordinary helm list releases. Use GitOps
history and Argo status for the full delivery track; helm rollback is not this
project's application recovery workflow.

Read [Helm delivery](https://github.com/Local-Narx-Space/boutique-gitops/blob/main/docs/helm-delivery.md)
when you are ready to understand chart checksums and immutable promotion.

## 12. Understand current Jenkins and GitOps delivery

Read [Jenkins setup](jenkins-setup.md) for controller/library/credentials and
[the deployment sequence](deploy-cicd-kind.md) for exact UI fields and commands.
Service Jenkinsfiles call `servicePipeline`; the shared library owns quality
and publication stages. `seed-release.Jenkinsfile` calls Job DSL to create jobs.
Job DSL creates jobs, while Pipeline DSL describes what their builds do.

The seed uses the reviewed CI commit. Main builds use protected service source.
Publishing occurs only after the separate Test stage, source/image scans and Helm
validation pass. Image digests identify immutable registry content; source-SHA
chart versions and packages identify exactly what Argo renders. Build artifacts
preserve scan/SBOM and source information. No custom signature files are needed.

Jenkins opens a GitOps PR and stops. Jenkins checks validate Helm/manifests with
read-only repository access. Independent human review is the approval boundary.
Argo deploys merged desired state. Rollout and functional smoke tests happen
separately; an image in Git does not prove that environment successfully ran it.

## 13. Migrate an existing Jenkins setup

1. Preserve the home volume and connected rootless build agent.
2. Update the CI/GitOps/platform checkouts without discarding local changes.
3. Review and pin the simplified CI commit as the untrusted shared-library version.
4. Use the Pipeline seed with the same reviewed commit and automatic builds suppressed.
5. Disable old Freestyle seeds, boutique-bootstrap and boutique-gitops-check;
   the seed preserves unrelated/old jobs instead of silently deleting them.
6. Remove the old required boutique/gitops-policy status from GitHub rules and
   require boutique/gitops-validation after the first Jenkins check has run.
7. After old builds finish, remove four release signing/public key credentials;
   retain publishing credentials and gitops-checks for manifest statuses.
8. Review bot/reviewer identities before enabling required review. Self-approval
   cannot satisfy independent production approval.

## 14. First deployment versus routine release

First deployment needs a complete selection of all four services. Publish each
and merge its dev selection before initial Argo sync. App secrets/data and
controllers must exist. No special aggregate bootstrap job is needed.
A routine release changes one service and preserves the others. Promote the same
image/chart from dev to staging, then staging to production, after confirming
the preceding environment passed rollout/smoke checks. Never rebuild for prod.

## 15. Environment separation

The laptop registry profile is dev-only with local persistent PostgreSQL/Redis.
Dev/staging can share a nonproduction cluster with separate namespaces, secrets
and data. Production belongs on a separate cluster/account and managed/HA data.
The existing kindnet cluster does not enforce NetworkPolicy. On a larger host,
the optional two-cluster exercise adds policy-capable CNI and TLS, but single
control-plane/data replicas still do not prove high availability.

## 16. Verification and recovery

Inspect Argo synchronized revision and Deployment readiness, then run the full
functional smoke suite. The environment deployment job's verify action uses scoped read-only
kubeconfigs and public TLS CA files. It archives operational reports; it does
not sign them or require another deployment agent to wait for it.
Rollback opens a PR restoring a service's known-good digest/chart from protected
Git history. Review migration compatibility. Database corruption requires data
restore, not only reverting an image. Keep off-host backups and measure restore
time/data loss against the documented RTO/RPO.

## 17. Why some production work remains

Local validation cannot prove GHCR rights, live GitHub branch protection,
application rollout, real Slack delivery or recovery time. Configure and execute
those checks on the target host. Use production secrets/permissions, HA and
network boundaries appropriate to the environment rather than inferring them
from a namespace or a green schema check.

> Monitoring note: Kubernetes now defaults to Slack-only routing. The ServiceNow
> worker/queue instructions in this chapter are optional and require
> `incidentBridge.enabled=true`; they are not deployment prerequisites.

## 18. Install and understand monitoring

### 18.1 Understand the signal flow

~~~mermaid
flowchart LR
  App[Application metrics endpoints] --> P[Prometheus]
  P --> G[Grafana dashboards]
  P --> R[Prometheus alert rules]
  R --> A[Alertmanager: group, inhibit, silence, route]
  A --> S[Slack receiver]
  A --> B[Authenticated incident bridge]
  B --> Q[Durable queue]
  Q --> W[Delivery workers]
  W --> SN[ServiceNow incident lifecycle]
  Logs[Container log files] --> Alloy[Grafana Alloy]
  Alloy --> Loki[Loki]
  Loki --> G
~~~

**Metrics** are numbers over time: requests, latency, errors and queue depth.
**Logs** are individual events: a request failed with a particular request ID.
**Alerts** are evaluated conditions requiring attention.
**Incidents** are response records with an owner, investigation and resolution.
Grafana displays signals; it does not replace Prometheus or durable storage.

### 18.2 Start the Compose monitoring exercise

Use this with the **Compose app**, not as a substitute for scraping kind Pods.
If you previously stopped Compose to use the single-kind app, restart the
Compose path first and stop its conflicting 8080 port-forward.

~~~bash
cd "$BOUTIQUE_ROOT/boutique-platform"
./scripts/local-up.sh
python3 monitoring/init-local.py
docker compose --env-file local/.env \
  -f local/compose.yaml -f monitoring/compose.yaml up -d --build
docker compose --env-file local/.env \
  -f local/compose.yaml -f monitoring/compose.yaml ps
~~~

In a second terminal, run:

~~~bash
cd "$BOUTIQUE_ROOT/boutique-platform"
bash monitoring/capture-local-logs.sh
~~~

This foreground process follows application logs and writes local files for
Alloy. Keep it running during the exercise; Ctrl-C stops capture.
Alloy does not need the Docker socket.

| Local address | What to inspect |
| --- | --- |
| http://127.0.0.1:8085 | Grafana Boutique dashboard |
| http://127.0.0.1:9090 | Prometheus targets, queries and rules |
| http://127.0.0.1:9093 | Alertmanager alerts, routing and silences |
| http://127.0.0.1:18080 | Local Slack/ServiceNow receiver fixture |

Grafana username is admin. Read its generated password privately from
monitoring/.secrets/grafana_password. The initializer preserves it.
Changing that file after Grafana's database is initialized does not by itself
reset Grafana's stored admin password.

Run the notification fixture exercise:

~~~bash
cd "$BOUTIQUE_ROOT/boutique-platform"
python3 monitoring/test_delivery.py
bash monitoring/validate.sh
~~~

The fixture is an unauthenticated, ephemeral test receiver, bound to loopback.
These commands exercise local delivery; they do not create real external
Slack messages or ServiceNow incidents.

### 18.3 Read the dashboard panel by panel

The provisioned dashboard has thirteen panels:

| Panel | How to interpret it |
| --- | --- |
| Service availability | Whether Prometheus can scrape expected service targets |
| Requests per second | Traffic rate; a quiet lab can legitimately have zero |
| HTTP error ratio | Fraction of observed requests returning errors |
| HTTP p95 seconds | Latency below which 95% of observations fall |
| Firing alerts | Current evaluated alert conditions |
| Pending incidents | Durable delivery backlog |
| Oldest incident seconds | How long the oldest pending delivery has waited |
| JVM heap bytes | Orders JVM memory usage |
| Frontend heap bytes | Frontend process heap usage |
| Application logs | Loki log view for investigation |
| Incident dead letters | Deliveries that exhausted retry attempts |
| Incident worker heartbeat age seconds | Detects a stuck worker even if its HTTP endpoint exists |
| Incident bridge scrape availability | Visibility of individual adapter replicas |

Select the intended cluster/environment. Do not combine dev load with
production measurements and then call the result production availability.

In Prometheus, try:

~~~promql
up
~~~

Then:

~~~promql
sum by (service) (rate(boutique_http_requests_total[5m]))
~~~

No result can mean missing telemetry or no applicable series, not necessarily
healthy zero traffic. Inspect the Targets page first.

For the Kubernetes log configuration, the Loki labels include cluster,
environment, service and container. For example:

~~~logql
{cluster="nonprod", environment="dev", service="cart"}
~~~

Request IDs are log fields, not metric labels. Unbounded labels such as order
IDs and session IDs would create excessive metric series.

### 18.4 Understand the alert rules

| Rule | Operational meaning |
| --- | --- |
| BoutiqueServiceDown | Expected application service cannot be scraped |
| BoutiqueHighErrorRate | Sustained request error ratio above the rule threshold |
| BoutiqueHighLatency | Sustained response latency above the rule threshold |
| AlertmanagerDeliveryFailures | The notification system cannot deliver successfully |
| PrometheusRuleFailures | Rule evaluation itself is failing |
| IncidentDeliveryBacklog | Queue items have waited too long |
| IncidentDeliveryDeadLetters | At least one durable delivery needs operator recovery |
| IncidentWorkerStalled | A worker heartbeat is stale |
| IncidentBridgeUnavailable | Discovered bridge replica cannot be scraped |
| IncidentBridgeMissing | Expected bridge targets are absent entirely |

Read monitoring/rules.yml for exact expressions, traffic floors and durations.
A threshold alert is different from an SLO error-budget burn alert.

Alertmanager groups related signals and inhibits matching warning alerts when
a critical one is firing. Normal Kubernetes repeat notifications are limited
to four hours; local fixtures use much shorter timings to make exercises quick.
An expiring silence needs an owner and reason. A silence suppresses notification,
not the underlying failure.

All configured alerts route to Slack. Critical **production application**
alerts additionally route to ServiceNow. Alerts about the incident-delivery
path carry incident_delivery=disabled and remain Slack-only, avoiding a
recursive queue of incidents about the queue being broken.

### 18.5 How the incident adapter survives failures

The adapter first authenticates an Alertmanager batch and durably commits
it, then acknowledges it. Delivery to ServiceNow happens separately.

| Profile | Queue | Workers | Receiver mode |
| --- | --- | --- | --- |
| Affordable local/homelab | SQLite WAL volume | Exactly one replica | Table API |
| Production learning profile | Dedicated TLS PostgreSQL | Two replicas | Scripted lifecycle endpoint |

Production workers use transactional locked-row claims, 60-second leases,
revision-safe acknowledgements and capped backoff. A crashed worker's lease
can expire so another worker retries the item.
After twelve failed attempts, an item becomes a durable dead letter; fixing
the receiver and using the authenticated requeue workflow is the recovery path.

Delivery is **at least once**: a remote write can succeed while its response is
lost, so the caller retries. The receiver's lifecycle identity and tombstones
handle duplicates and a resolved event arriving before a delayed firing event.
Do not delete the queue or completed tombstones to make an alert disappear.

Two adapter replicas do not make the queue database HA. The local production
queue PostgreSQL is still single replica.

### 18.6 Deploy monitoring in the full kind lab

Complete the relevant application baselines first. Nonprod monitoring expects
dev and staging; production monitoring expects production.

1. Enable `ENABLE_INCIDENT_BRIDGE_BUILD=true` in the Pipeline seed, then run
   boutique-platform/main on the trusted build agent. Its shared service
   pipeline builds **incident-bridge**, including real PostgreSQL integration.
2. Inspect the successful published release and capture the image digest.
3. Open a reviewed GitOps PR changing the adapter image values in
   monitoring/profiles/nonprod/values.yaml and
   monitoring/profiles/production/values.yaml.
4. Add/update the image object under incidentBridge without removing the
   existing production spec/replica configuration:

~~~yaml
incidentBridge:
  image:
    repository: ghcr.io/subhankar12-spec/boutique-incident-bridge
    digest: sha256:REPLACE_WITH_THE_ACTUAL_64_HEX_DIGEST
    tag: ""
~~~

The string above is explanatory and must be replaced with the measured release
digest. The default replace-with-tested-digest tag deliberately fails the
full-lab immutable-image check.

The lab monitoring values supplement these cloud profile values with internal
receiver fixtures and local data/TLS settings. Changing the image is a reviewed
monitoring GitOps change; the application promote job accepts only the four
application services.

After manifest validation, CODEOWNER review and merge:

~~~bash
git -C "$BOUTIQUE_ROOT/boutique-gitops" pull --ff-only origin main
cd "$BOUTIQUE_ROOT/boutique-platform"

python3 ../boutique-gitops/scripts/render-monitoring.py nonprod --lab \
  > /tmp/boutique-monitoring-nonprod.yaml
python3 ../boutique-gitops/scripts/render-monitoring.py production --lab \
  > /tmp/boutique-monitoring-production.yaml

python3 scripts/production-lab.py deploy --cluster nonprod --monitoring
python3 scripts/production-lab.py deploy --cluster production --monitoring
~~~

Wait for **lab-monitoring-nonprod** and **lab-monitoring-production** to be
Synced/Healthy, then:

~~~bash
python3 scripts/production-lab.py readiness --cluster nonprod --monitoring
python3 scripts/production-lab.py readiness --cluster production --monitoring
~~~

The lab's earlier secrets command prepares fixture integration/Grafana secrets,
and production queue credentials/trust. Cloud deployment needs independently
prepared real receiver/database secrets.

Expose nonprod admin interfaces locally, each in its own terminal:

~~~bash
kubectl --context kind-boutique-nonprod -n monitoring \
  port-forward --address 127.0.0.1 service/grafana 18085:3000
~~~

~~~bash
kubectl --context kind-boutique-nonprod -n monitoring \
  port-forward --address 127.0.0.1 service/prometheus 19090:9090
~~~

~~~bash
kubectl --context kind-boutique-nonprod -n monitoring \
  port-forward --address 127.0.0.1 service/alertmanager 19093:9093
~~~

Use the production context and different local ports if forwarding both
clusters simultaneously. Obtain Grafana's password from the corresponding
monitoring-integrations Secret using operator access privately.

Alloy runs in a separate monitoring-logs namespace with a reviewed exception
for read-only host log files and root file reading. It drops capabilities and
cannot escalate privileges. This exception must not be copied to app Pods.
Log position state can replay duplicates after node restart.

### 18.7 Prepare real Slack and ServiceNow separately

The default lab uses mocks. For live activation:

- Create an approved Slack incoming webhook for a dedicated alert channel.
  Store its URL as the slack_webhook secret, never in tracked YAML.
- Create a ServiceNow developer instance using ITSM Incident Management.
  It teaches lifecycle/correlation without requiring ITOM licensing, but can
  sleep/reset and is not a production service.
- Create a dedicated integration user with the necessary incident/lifecycle
  ACLs; do not use an administrator account.
- Review and install monitoring/servicenow/scripted-rest-resource.js,
  its lifecycle table, correlation field and required unique indexes.
- Configure the real HTTPS instance URL, username, password secret and your
  instance's valid close-code/required-field choices.
- Validate the endpoint in that instance before replacing mock receivers.
  Changing only the URL does not install its server-side lifecycle logic.
- Run an intentional firing/resolution/retry exercise in the approved channel
  and instance, recording the incident identity and resolution.

The supplied Table API mode uses Basic authentication over verified HTTPS;
organizations may require OAuth and additional ACL/business-rule changes.
No real remote notification has been activated by this guide.

### 18.8 What this monitoring stack does not yet prove

The handwritten Prometheus, Alertmanager, Grafana and Loki stack is single
replica. A production-reference kube-prometheus-stack values file is a separate
reference, not an automatically installed second stack.

Continuous external journey probes, automated SLO burn alerts, on-call paging,
external dead-man detection, long-term object storage, SSO and backup automation
still need deployment and validation.
Read [monitoring](monitoring.md) for receiver and queue recovery details.

## 19. Understand the AWS reference

You do not need an AWS account for the kind learning path.
The AWS code is a realistic managed-infrastructure reference and has not been
applied. It can create substantial ongoing charges.

### 19.1 Read the infrastructure in dependency order

~~~mermaid
flowchart TD
  State[Restricted versioned Terraform state backend] --> Roots[Separate nonprod / production roots]
  Roots --> VPC[Three-AZ VPC and subnet routing]
  VPC --> EKS[Private EKS API and workers]
  VPC --> DB[Managed PostgreSQL and Redis]
  Roots --> KMS[Encryption keys and Secrets Manager]
  EKS --> IAM[OIDC / IRSA identities]
  IAM --> ESO[External Secrets controller: operator installs]
  ESO --> Sec[Kubernetes Secrets]
  Sec --> Apps[Application Pods]
  EKS --> Boot[Argo, ingress, TLS: operator bootstrap]
  Boot --> Apps
  Audit[Separate once-per-account audit root] --> Trail[CloudTrail and audit destinations]
~~~

Terraform state records which resources it manages and their current attributes.
A plan compares desired configuration to state and AWS; apply performs the
approved changes. State can contain generated secrets even when CLI output
marks them sensitive.

The top-level roots select independent environment settings/state. Both call
the same modules/platform directory. Terraform combines all .tf files in a
directory into one module; filenames organize code, not execution order.

| Platform module file | What to read there |
| --- | --- |
| main.tf | Availability-zone selection and tags |
| networking.tf | VPC module, subnets, NAT and database security rules |
| eks.tf | Private cluster, workers, access entries and core addons |
| iam.tf | IAM/OIDC and External Secrets roles |
| encryption.tf | Platform data key/alias |
| databases.tf | PostgreSQL and Redis settings |
| secrets.tf | App credentials and Secrets Manager values |
| monitoring.tf | Dedicated queue DB and monitoring secret roles |
| observability.tf | Logs, alarms, dashboard, SNS and optional insights |
| operator.tf | Private SSM-only operator host |
| budgets.tf | Tagged monthly budget notifications |
| variables.tf / outputs.tf / versions.tf | Input/output/provider contracts |

The VPC is a version-pinned terraform-aws-modules/vpc/aws child module.
Its underlying subnet/route/NAT resources are downloaded on init, so you will
not see every raw aws_subnet resource in your local networking.tf.
Read the [module map](https://github.com/Local-Narx-Space/boutique-infrastructure/blob/main/modules/platform/README.md).

### 19.2 Networking and managed data

| Concern | Nonprod | Production |
| --- | --- | --- |
| Separate VPC | 10.10.0.0/16 | 10.20.0.0/16 |
| Availability zones | Three | Three |
| Subnet classes | Public, private workers, private data | Same separation |
| NAT gateways | One, cost-saving availability compromise | One per AZ |
| EKS API | Private | Private |
| Orders RDS | Per application environment, shorter backup retention | Multi-AZ, 14-day backups |
| Redis | TLS/authenticated, smaller availability profile | TLS/authenticated, Multi-AZ failover/replicas |
| Optional incident queue RDS | Separate DB, 7-day retention | Enabled by default, separate Multi-AZ DB, 35-day retention |

A private EKS API cannot be administered directly from arbitrary laptop
internet access. You need a reviewed private network path and authorized role.
The operator host uses SSM with no inbound SSH; its basic instance role does
not itself make it Kubernetes cluster-admin.

Orders and queue databases are distinct. Their admin credentials and
least-privilege application roles are distinct too.
The app owns its schema and runs Flyway migrations; a separate migration
identity is a further hardening step.

### 19.3 IRSA, ESO and CSI: different layers

**IRSA** answers: “Which AWS role may this Kubernetes service account assume?”
The EKS OIDC issuer plus IAM trust conditions bind the role to the intended
namespace/service-account subject.

**External Secrets Operator, ESO** answers: “How does an external secret become
a Kubernetes Secret?” It reads Secrets Manager using the configured role and
reconciles Kubernetes Secrets that applications reference.

**Secrets Store CSI Driver** is another delivery approach: it can mount external
secrets into a Pod filesystem, with optional synchronization depending on setup.
This project uses ESO for AWS secret delivery; it does not configure that
secrets CSI driver.

IRSA and CSI are not alternatives at the same layer. A CSI provider could also
need an AWS workload identity. Here the chosen combination is **IRSA + ESO**.

The EKS Pod Identity agent addon appears in the reference, but no workload
associations are configured to replace the current IRSA roles.

Storage CSI is a separate topic again. An EBS CSI provisioner is needed if
you choose EBS-backed Kubernetes PVCs. Do not assume installing a secrets
controller provisions persistent disks; review the cloud storage driver and
StorageClasses before applying monitoring PVCs.

### 19.4 CloudWatch versus CloudTrail

| Service | Question it helps answer | This project's configuration |
| --- | --- | --- |
| CloudWatch | Is infrastructure/application infrastructure healthy? | EKS control-plane logs, VPC flow logs, DB/cache logs, alarms, dashboard and SNS |
| CloudTrail | Who performed which AWS API action? | Separate account-audit root with multi-region management-event trail |
| Prometheus/Grafana | How are application requests and incident delivery behaving? | App/runtime monitoring deployed separately |
| Loki/Alloy | What did application containers log? | Separate app log collection |

The CloudWatch reference includes encrypted EKS control-plane log groups,
network flow logs, PostgreSQL/upgrade logs and Redis slow/engine logs.
Service log retention differs by environment, with longer production retention.
RDS CPU/free-storage and Redis CPU/memory alarms notify through encrypted SNS.
The email subscription must be confirmed; a Terraform resource alone does not
prove someone receives an alarm.

Optional Container Insights is disabled by default and requires reviewed addon
compatibility/identity/cost configuration. Automatic language instrumentation
is not enabled by the reference.

The separate audit root configures management events across regions,
global service events, trail log validation, KMS, a private versioned S3
destination, CloudWatch logs and security/configuration alarms.
Default S3 audit retention is 365 days with a 90-day archival transition;
the log group has its own retention.
S3 data events are an optional scoped choice, not blanket default collection.
This is not an organization-wide trail or a deployed cross-region DR system.

Read [AWS observability](https://github.com/Local-Narx-Space/boutique-infrastructure/blob/main/docs/aws-observability.md).

### 19.5 Validate Terraform without provisioning

These checks do not require applying resources or entering AWS keys:

~~~bash
cd "$BOUTIQUE_ROOT/boutique-infrastructure"
terraform fmt -check -recursive
terraform -chdir=environments/nonprod init -backend=false -lockfile=readonly
terraform -chdir=environments/nonprod validate
terraform -chdir=environments/production init -backend=false -lockfile=readonly
terraform -chdir=environments/production validate
~~~

Init downloads providers/modules. It still needs network/disk and compatible
provider locks. If restored .terraform cache paths are invalid, use a fresh
TF_DATA_DIR outside Git instead of editing/removing provider locks.

~~~bash
mkdir -p "$HOME/.cache/boutique/terraform-nonprod"
TF_DATA_DIR="$HOME/.cache/boutique/terraform-nonprod" \
  terraform -chdir=environments/nonprod init -backend=false -lockfile=readonly
TF_DATA_DIR="$HOME/.cache/boutique/terraform-nonprod" \
  terraform -chdir=environments/nonprod validate
~~~

Use a different data directory for production/audit. This validates configuration
structure, not AWS quotas, IAM access, regional version availability or runtime.

### 19.6 Understand the eventual apply sequence

Before any AWS apply:

1. Select account/region, compatible versions, private access and a reviewed cost
   estimate. Budget alerts notify; they do not stop spending.
2. Bootstrap the encrypted/versioned state bucket, preserve initial bootstrap
   state and migrate it to its own remote key.
3. Prepare backend/tfvars from each root's examples, replacing example account,
   email, bucket and role values. Never place AWS keys there.
4. Configure short-lived agent identity and scoped state/resource permissions.
5. Run protected Terraform planning on terraform-trusted.
6. Review the redacted resource-action summary and approve the exact saved plan
   as platform-admin.
7. Initialize application/queue data roles over the private network using
   verified CA trust.
8. Install/configure Argo, ESO, ingress/TLS controllers and storage drivers.
9. Configure real DNS, certificates, secret roles and approved releases.
10. Measure actual deployment, notification, backup and recovery behavior.

The saved binary plan stays in a private directory and is not archived because
it can contain secrets. Approval applies that same plan; it is not permission
to generate an unrelated plan later.

The current backend uses versioned private S3, TLS enforcement and native S3
lock files. Nonprod, production and audit have distinct state keys.
Backend encryption does not remove the need to restrict state access/backups.

Terraform does not provision every platform controller/agent automatically.
The [infrastructure README](https://github.com/Local-Narx-Space/boutique-infrastructure/blob/main/README.md)
is the starting point for that separate, paid deployment.

## 20. Practice failures, backup and recovery

### 20.1 Understand SLO, RTO and RPO with this app

| Term | Plain meaning | Example here |
| --- | --- | --- |
| SLI | Measured reliability indicator | Fraction of eligible frontend responses that succeed |
| SLO | Target for that indicator | 99.9% API response availability over 30 days |
| Error budget | Allowed bad events within the objective | At one million eligible requests, 0.1% allows 1,000 failed responses |
| RTO | Time from user-impacting failure until verified recovery | Intended 30-minute compatible app rollback |
| RPO | Accepted loss of committed data | Intended zero lost committed DB writes for app-only rollback |

The documented objectives include 99.9% API/checkout response availability
and 99% of successful eligible API responses within one second.
The window is rolling 30 days.

Current Prometheus retention is seven days. You cannot truthfully report a
complete 30-day SLO from seven days of history. Extend reviewed retention/
storage to at least 35 days or add equivalent durable aggregation first.
Missing telemetry or zero traffic is unknown, not 100% success.

Frontend response metrics cannot observe every DNS/TLS failure or a request
that never reaches/completes at the server. A release smoke test is not a
continuous external availability probe.
Read [SLO definitions and queries](slo.md).

### 20.2 Recovery targets are plans to prove

| Scope | Intended RTO | Intended RPO | Important condition |
| --- | --- | --- | --- |
| Compatible application rollback | 30 minutes | Zero committed DB writes | Retained image/chart versions and prior rollout reports |
| Laptop/VM local data loss | 4 hours | 24 hours | Daily consistent encrypted off-host backups |
| AWS orders or dedicated queue PITR | 60 minutes | 15 minutes | Actual latest-restorable-time and verified restore/cutover |
| Redis carts | 60 minutes | 24 hours | Valid snapshot/persistence restore and matching credentials |
| Jenkins config/history | 4 hours | 24 hours | Separate controller backups and accepted release retention |
| Rebuild cluster around recoverable managed data | 4 hours | Data-store objectives | Reproducible config, access, trust, registry and backups |

These are not achieved measurements. A 14-day backup-retention setting does
not guarantee a 15-minute RPO. Measure the actual data recovery point.
Multi-AZ availability reduces some failures; it is not regional disaster recovery.

### 20.3 Back up and restore the Compose orders database

Start the Compose app for this exercise. Do not point these Compose helpers
at a kind Pod.

~~~bash
umask 077
mkdir -p "$HOME/boutique-backups"
chmod 700 "$HOME/boutique-backups"
cd "$BOUTIQUE_ROOT/boutique-platform"
./scripts/backup-local.sh "$HOME/boutique-backups/orders.dump"
./scripts/restore-drill.sh "$HOME/boutique-backups/orders.dump"
~~~

The backup is PostgreSQL's consistent custom-format logical dump.
The first helper checks archive readability, not complete data recovery.
The second drops/recreates **boutique_restore_drill**, a disposable database,
and restores into it; it does not overwrite the live boutique database.
Do not run concurrent drills using that same disposable name.

Before backup, record representative order IDs/counts and UTC times.
After restore, compare recovered data with those records.
A row count alone does not prove the latest acknowledged write survived.

The destination above is on your laptop. It is useful for the exercise but does
not survive loss of that laptop. Implement encrypted independent storage,
scheduling, failure/age alerts and periodic restore validation to meet the
off-host objective.

### 20.4 Practice the kind lab's recovery and policy drills

Use dev first:

~~~bash
cd "$BOUTIQUE_ROOT/boutique-platform"
python3 scripts/production-lab.py policy-drill --environment dev
python3 scripts/production-lab.py restore-drill --environment dev
~~~

The policy drill tests permitted ingress and denied unrelated access on the
enforcing CNI. It proves more than merely finding a NetworkPolicy YAML file.

The restore drill uses an isolated restore target and checks orders plus
migration history. Its measured database restore step is only part of end-to-end
RTO. Detection, provisioning, access, TLS, cutover and functional acceptance
also count.

To observe self-healing in dev, inspect Pods and choose one application Pod:

~~~bash
kubectl --context kind-boutique-nonprod -n boutique-dev get pods
~~~

Then delete only the chosen **dev** application Pod:

~~~bash
# Replace this name with one actual application Pod, not a database Pod.
kubectl --context kind-boutique-nonprod -n boutique-dev delete pod ACTUAL_APP_POD_NAME
kubectl --context kind-boutique-nonprod -n boutique-dev get pods --watch
~~~

The Deployment recreates it. Inspect readiness, request failures and metrics.
This can cause brief dev downtime; it is not a production disaster drill.

Node drain, database loss and host-loss exercises need reviewed scope, verified
backups and an isolated target. Local-volume database Pods cannot simply move
with their data to another worker. Do not begin by deleting PVCs/namespaces.

### 20.5 What to preserve besides PostgreSQL

| State | Why Git alone cannot restore it |
| --- | --- |
| Orders and incident queue, including lifecycle tombstones | Runtime committed data |
| Redis persistence | Existing anonymous carts |
| Session signing keys | Access to users' existing sessions/order history |
| CA private keys and matching trust | Continuity of verified lab certificates |
| Jenkins home volumes | Credentials encryption material, history and job state |
| Published images, packaged charts and rollout/smoke reports | Reconstruct and verify a known-good release |
| Terraform state and access | Managed-resource ownership and generated sensitive values |
| Grafana storage / Alertmanager silences | Runtime admin state and maintenance context |

A source ZIP/Git bundle is a source backup, not a database/credential backup.
The whole incident queue matters; copying only pending rows loses deduplication
and resolution history.

Document each exercise with actual UTC start/end, data-loss comparison,
source/artifact versions, backup checksum/location and follow-up work.
Use [recovery exercise template](runbooks/recovery-exercise.md),
[recovery targets](recovery-targets.md) and
[disaster recovery](runbooks/disaster-recovery.md).
There is no deployed secondary region/account or automatic regional failover.

## 21. Troubleshoot in dependency order

Agent offline: inspect its Remoting logs, WebSocket controller URL and secret
file argument. Docker access failure: confirm the agent UID, socket owner and
rootless daemon. Do not grant the rootful docker group to solve it.

Build fails: inspect the first failed stage; distinguish tests, fixable scan
findings, tool/download access and registry permissions. Do not disable a gate
to force a green result. Existing commit tags cannot be overwritten.

GitOps PR fails: inspect boutique/gitops-validation. Check dependency package/version,
image selections, namespaces and resource kinds. Old boutique/gitops-policy
requirements must be migrated rather than waiting for a removed job.

ImagePullBackOff: confirm a real digest exists and GHCR visibility/pull secrets.
Pending data pods: inspect PVC/storage. CrashLoopBackOff: inspect previous logs
and Secret names; do not reset passwords while retaining existing databases.

Argo OutOfSync: inspect diff and source profile; an initial laptop Application
needs manual sync. Running pods with failing readiness: inspect internal service
URLs, Redis/database credentials and Flyway migrations. A wrong origin/port
causes mutation requests to fail. Laptop CI/CD uses http://localhost:8088.

## 22. Stop and resume safely

Stop port-forward with Ctrl+C. Preserve Jenkins home, rootless Docker data,
cluster containers, app PVCs and private credentials. Do not use volume deletion
as a troubleshooting shortcut. On resume, check the existing cluster/context,
agent, Argo state and app smoke test before making another release.

## 23. Validation and limits

[validation.md](validation.md) separates executed configuration/controller checks
from live acceptance. AWS and external integrations need actual deployment.
[SLOs](slo.md), [recovery targets](recovery-targets.md) and the
[DR runbook](runbooks/disaster-recovery.md) remain targets until measured.

## 24. Recommended execution order

Existing cluster/agent → reviewed library and credentials → Pipeline seed →
four publishing builds → reviewed GitOps selections → app secrets → initial
Argo sync → smoke → routine release/promotion → monitoring → rollback/restore.

## 25. Glossary

**Pipeline:** build workflow stored as a Jenkinsfile. **Shared library:** reusable
Groovy/Pipeline logic. **Job DSL:** version-controlled job configuration.
**Seed:** job executing Job DSL. **Digest:** immutable image content identifier.
**Helm chart:** Kubernetes templates/defaults/schema. **Values:** environment
settings. **GitOps:** Git as reviewed desired state. **Argo sync:** reconciliation
of that state into Kubernetes. **SLO:** reliability objective. **RTO:** target
recovery time. **RPO:** acceptable data-loss window. **IRSA:** AWS IAM permissions
for Kubernetes service accounts. **ESO:** sync from external secret storage.
