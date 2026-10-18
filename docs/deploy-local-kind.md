# Deploy Boutique on your Debian laptop: follow these steps

**Optional manual exercise.** For the selected main project workflow, use
[the CI/CD-first deployment guide](deploy-cicd-kind.md). That guide configures
the platform and Jenkins before Argo deploys the application.

**Goal:** open the storefront at **http://localhost:8080**, with its four
application services, Redis and PostgreSQL running in your kind cluster.

Run these commands **on your Debian laptop**. Use the same terminal for
steps 1–7, in order. Open a second terminal only when step 8 asks you to.
If a command fails, stop at that step and use the troubleshooting table below.

This exercise uses locally built images and the local Helm values.
It is independent of the CI/CD-first workflow; completing it is not required
before configuring Jenkins or starting the GitOps delivery path.

The work you will do is:

~~~text
Clone repositories
  → Install/check tools
  → Select your kind cluster
  → Build and load images
  → Create app secrets
  → Render and apply Kubernetes configuration
  → Wait for readiness
  → Open and test the storefront
~~~

## 1. Get the repositories

Your laptop needs Docker Engine with the Compose plugin working, Linux amd64
(x86_64), and enough memory/disk for kind and the image builds.

If Docker is not installed yet, first follow
[the Debian Docker installation section](beginner-guide.md#63-install-docker-engine-and-compose),
then return here. You do not need to run the Compose application first.

Install the basic command-line prerequisites:

~~~bash
sudo apt-get update
sudo apt-get install -y \
  git curl ca-certificates unzip openssl python3 python3-yaml

docker version
docker compose version
uname -m
~~~

**Check:** Docker shows both client and server, Compose shows a version,
and uname prints x86_64. Permission denied or a missing Docker server must
be resolved before proceeding.

Clone the eight repositories beside one another:

~~~bash
export BOUTIQUE_ROOT="$HOME/devops-boutique"
mkdir -p "$BOUTIQUE_ROOT"
cd "$BOUTIQUE_ROOT"

for repo in frontend catalogue cart orders ci infrastructure gitops platform; do
  if [ -e "boutique-$repo" ]; then
    printf 'Keeping existing directory: boutique-%s\n' "$repo"
  else
    git clone "https://github.com/Local-Narx-Space/boutique-$repo.git" || break
  fi
done
~~~

If you already cloned them into a different parent folder, set BOUTIQUE_ROOT
to that folder instead. An existing directory must actually be the intended
checkout; this command intentionally preserves existing files.

**Check:** each of these exists:

~~~bash
for repo in frontend catalogue cart orders ci infrastructure gitops platform; do
  git -C "$BOUTIQUE_ROOT/boutique-$repo" rev-parse --show-toplevel || break
done
~~~

You should see eight repository paths. These sibling names matter because
the build configuration refers to them.

## 2. Install the project's Kubernetes tools

~~~bash
cd "$BOUTIQUE_ROOT/boutique-platform"
./scripts/install-tools.sh "$PWD/.tools"
export PATH="$BOUTIQUE_ROOT/boutique-platform/.tools:$PATH"

kind version
kubectl version --client=true
helm version --short
kubeconform -v
~~~

**Check:** all four version commands work. The installer checks downloaded
artifacts before installing them.

## 3. Choose the kind cluster you want to use

List your clusters:

~~~bash
kind get clusters
~~~

Enter the exact name of the cluster you want to deploy into:

~~~bash
read -r -p "Kind cluster name from the list: " BOUTIQUE_KIND_CLUSTER
export BOUTIQUE_KIND_CLUSTER
export BOUTIQUE_KIND_CONTEXT="kind-$BOUTIQUE_KIND_CLUSTER"
~~~

For example, if the list says kind, type **kind**. If it says my-cluster,
type **my-cluster**. Do not type the context prefix kind-.

If the list is empty, create a dedicated cluster instead:

~~~bash
# Run this block only if you need a new cluster.
export BOUTIQUE_KIND_CLUSTER=boutique-learning
export BOUTIQUE_KIND_CONTEXT="kind-$BOUTIQUE_KIND_CLUSTER"
kind create cluster --name "$BOUTIQUE_KIND_CLUSTER" \
  --config "$BOUTIQUE_ROOT/boutique-platform/local/kind.yaml" \
  --image kindest/node:v1.34.0
~~~

Give this terminal a private kubeconfig for only that cluster:

~~~bash
install -d -m 0700 "$HOME/.local/share/boutique-learning"
umask 077
kind get kubeconfig --name "$BOUTIQUE_KIND_CLUSTER" \
  > "$HOME/.local/share/boutique-learning/kind-operator.yaml"
chmod 0600 "$HOME/.local/share/boutique-learning/kind-operator.yaml"
export KUBECONFIG="$HOME/.local/share/boutique-learning/kind-operator.yaml"

kubectl config current-context
kubectl get nodes
kubectl get storageclass
kubectl get namespace boutique-dev --ignore-not-found
~~~

**Check:** the context identifies the cluster you selected, nodes are Ready,
and there is a default StorageClass. Use a Kubernetes 1.34-compatible cluster.
If boutique-dev already exists, confirm it belongs to this project before
continuing; do not overwrite an unrelated workload.

## 4. Build the four application images and load them into kind

~~~bash
cd "$BOUTIQUE_ROOT/boutique-platform"
./scripts/init-local.sh
docker compose --env-file local/.env -f local/compose.yaml build
~~~

**Check:** the Docker build finishes successfully. The first build downloads
dependencies and packages application code; Java builds can take a while.
Runtime image builds do not run tests. Run each service README’s isolated test
commands first, or use Jenkins’s separate Test stage in the primary CI/CD path.

Compose is used here only as a convenient build definition. You have not
started a second copy of the app with this build command.

After that build succeeds:

~~~bash
for service in frontend catalogue cart orders; do
  docker tag "boutique-$service:latest" "boutique-$service:local" &&
    kind load docker-image "boutique-$service:local" \
      --name "$BOUTIQUE_KIND_CLUSTER" || break
done
~~~

**Check:** all four images load successfully. Kubernetes nodes have their own
image runtime; having an image in your laptop Docker daemon is not enough.

## 5. Create the application's Kubernetes secrets

~~~bash
cd "$BOUTIQUE_ROOT/boutique-platform"
python3 scripts/k8s-secrets.py dev
kubectl -n boutique-dev get secrets
~~~

**Check:** these Secret names are present:

~~~text
redis-auth
cart-redis
frontend-session
orders-database
~~~

The helper generates missing credentials and preserves existing ones.
Keep them with their initialized database storage. Listing their names is
sufficient; do not copy Secret values into Git or chat.

## 6. Deploy the Kubernetes configuration

Render the **local** values, then validate and apply:

~~~bash
python3 "$BOUTIQUE_ROOT/boutique-gitops/scripts/render.py" dev \
  --profile local > /tmp/boutique-dev-local.yaml &&
kubeconform -strict -summary -kubernetes-version 1.34.0 \
  /tmp/boutique-dev-local.yaml &&
kubectl apply -f /tmp/boutique-dev-local.yaml
~~~

The && connections ensure a failed render/schema check stops the apply.

**Check:** schema validation reports no invalid resources/errors and kubectl
reports resources created/configured.

The rendered Helm configuration already includes PostgreSQL and Redis.
Do not separately install the optional dependencies/homelab chart into this
namespace.

## 7. Wait for the application to become ready

~~~bash
for service in frontend catalogue cart orders; do
  kubectl -n boutique-dev rollout status \
    "deployment/$service" --timeout=300s || break
done

kubectl -n boutique-dev get pods
kubectl -n boutique-dev get pvc
~~~

**Check:** all four Deployments finish rolling out, application/data Pods are
Ready, and PVCs are Bound. If a rollout times out, inspect the failure before
continuing.

## 8. Open the storefront and run its functional test

Port 8080 must be available. If you previously started the Compose app, stop
that copy without deleting its volumes:

~~~bash
# Only needed if the earlier Compose app is still running.
cd "$BOUTIQUE_ROOT/boutique-platform"
docker compose --env-file local/.env -f local/compose.yaml down
~~~

If you started Compose monitoring too, use the combined stop command in
[the full guide](beginner-guide.md#22-stop-and-resume-safely).
Do not add -v.

In your current terminal:

~~~bash
kubectl -n boutique-dev port-forward \
  --address 127.0.0.1 service/frontend 8080:8080
~~~

Leave this running. **Open http://localhost:8080 in your browser.**
Use that exact address; the frontend's allowed browser Origin uses localhost.

In a **second terminal**, set the workspace path again:

~~~bash
export BOUTIQUE_ROOT="$HOME/devops-boutique"
cd "$BOUTIQUE_ROOT/boutique-platform"
BASE_URL=http://localhost:8080 python3 tests/smoke/smoke.py \
  --environment dev --report /tmp/boutique-kind-smoke.json
~~~

If you used a different workspace path, use it here too.
This terminal needs no kubeconfig because the test calls the HTTP app.

**You are done with the first deployment when:**

- You can browse products, add items to a cart and place a test order.
- The smoke test exits successfully.
- All four application Deployments are ready and database PVCs are Bound.

The smoke test creates synthetic orders in your lab database.

## If something fails

Use the original terminal's selected kubeconfig for Kubernetes commands.
If you open a new operator terminal, re-export KUBECONFIG from step 3.

| Problem | What to do |
| --- | --- |
| Docker permission denied / server unavailable | Fix Docker installation/user access before building |
| Build failed | Read the failed build stage; do not proceed to image loading |
| ImagePullBackOff | Confirm all four local images loaded into the selected cluster and you rendered --profile local |
| PVC Pending | Inspect StorageClass/provisioner and PVC events; databases need storage |
| Pod crashes / rollout timeout | Inspect events and current/previous logs |
| Address already in use | Stop the earlier Compose frontend or conflicting port-forward; keep the app port/origin consistent |
| Browser loads but mutations fail | Use http://localhost:8080 and check frontend logs |
| NetworkPolicy blocks access | Inspect your CNI/policies; default kind networking does not enforce policies, and policy-enabled existing clusters require connectivity validation |

~~~bash
kubectl -n boutique-dev get events --sort-by=.metadata.creationTimestamp
kubectl -n boutique-dev get pods
kubectl -n boutique-dev logs deployment/orders --tail=100
kubectl -n boutique-dev logs deployment/frontend --tail=100
~~~

Share the failed step and redacted error if you need help. Do not delete
database PVCs or generate new database passwords as a troubleshooting shortcut.

## What comes after the app works?

This is the first Kubernetes milestone. Next, follow the full guide in this order:

1. [Jenkins architecture and stages](beginner-guide.md#12-understand-current-jenkins-and-gitops-delivery).
2. [Controllers and agents](jenkins-setup.md).
3. [Credentials and branch protection](deploy-cicd-kind.md#4-configure-the-shared-library-and-credentials).
4. [Two-cluster foundations](production-lab.md).
5. [First dev deployment and promotion](deploy-cicd-kind.md#6-build-and-publish-the-four-services).
6. [Monitoring](beginner-guide.md#18-install-and-understand-monitoring).

Those steps use different clusters and immutable registry releases. Keep the local
image exercise and the production delivery workflow distinct.

Ctrl-C stops the port-forward when you finish; the app keeps running in kind.
Deleting the kind cluster deletes its local data, so keep it until you deliberately
decide that data is disposable.
