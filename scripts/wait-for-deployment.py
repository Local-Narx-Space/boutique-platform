#!/usr/bin/env python3
"""Wait for the approved Argo revision and all four native Deployment rollouts.

A later main-branch revision is acceptable only when trusted full Git history
proves ancestry and the selected service still uses the requested image digest.
Only read-only Kubernetes calls are made. stdout contains the verified full SHA.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import posixpath
import yaml
from pathlib import Path
import re
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from progressive_deployment import ready as progressive_ready, ProgressiveError

REPOSITORY = 'https://github.com/Local-Narx-Space/boutique-gitops.git'
SERVICES = ('frontend', 'catalogue', 'cart', 'orders')
ENVIRONMENTS = ('dev', 'staging', 'production')
FULL_SHA = re.compile(r'[0-9a-f]{40}')


class DeploymentError(ValueError):
    pass


def command(arguments, timeout=30):
    try:
        result = subprocess.run([str(argument) for argument in arguments], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DeploymentError(f'{Path(str(arguments[0])).name} read failed or timed out') from error
    if result.returncode:
        # Never echo native object bodies, credential material or raw tool errors.
        raise DeploymentError(f'{Path(str(arguments[0])).name} read failed (exit {result.returncode})')
    return result.stdout


def load_release_config(root):
    source = Path(root) / 'scripts/release_config.py'
    if not source.is_file():
        raise DeploymentError('Trusted GitOps release helper is missing')
    sys.path.insert(0,str(source.parent))
    spec = importlib.util.spec_from_file_location('boutique_wait_release', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TrustedHistory:
    def __init__(self, root, environment, service, image, requested):
        self.root = Path(root)
        self.progressive_frontend = False
        self.progressive_services = ()
        self.environment, self.service, self.image, self.requested = environment, service, image, requested
        if environment not in ENVIRONMENTS or service not in SERVICES:
            raise DeploymentError('Invalid environment or service')
        if not FULL_SHA.fullmatch(requested):
            raise DeploymentError('A full requested GitOps commit SHA is required')
        if not re.fullmatch(rf'ghcr\.io/subhankar12-spec/boutique-{service}@sha256:[a-f0-9]{{64}}', image):
            raise DeploymentError('The selected service requires its immutable GHCR image digest')
        if self.git('rev-parse', '--is-shallow-repository') != 'false':
            raise DeploymentError('Full Git history is required; a shallow checkout cannot establish trusted ancestry')
        if self.git('remote', 'get-url', 'origin') != REPOSITORY:
            raise DeploymentError('Git origin does not match the approved GitOps repository')
        self.release_config = load_release_config(self.root)
        self.protected_tip = self.git('rev-parse', '--verify', 'refs/remotes/origin/main^{commit}')
        if not FULL_SHA.fullmatch(self.protected_tip):
            raise DeploymentError('A full protected origin/main revision is required')
        self.ancestor(requested, self.protected_tip)
        self.match_image(requested)
        from helm_release import selected_chart
        self.requested_chart=selected_chart(self.root,self.service,self.environment,requested)

    def git(self, *arguments):
        return command(['git', '-C', self.root, *arguments]).strip()

    def is_ancestor(self, earlier, later):
        try:
            result = subprocess.run(['git', '-C', str(self.root), 'merge-base', '--is-ancestor', earlier, later],
                                    capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DeploymentError('Trusted Git ancestry read failed') from error
        if result.returncode not in (0, 1):
            raise DeploymentError('Trusted Git ancestry read failed')
        return result.returncode == 0

    def ancestor(self, earlier, later):
        if not self.is_ancestor(earlier, later):
            raise DeploymentError('Argo/requested revision is outside the fetched protected main history or ancestry')

    def match_image(self, revision):
        try:
            selected = self.release_config.selected_image(self.root, self.service, self.environment, revision)
        except (ValueError, OSError, KeyError, TypeError, ImportError) as error:
            raise DeploymentError('Selected service image is unavailable in trusted GitOps history') from error
        if selected != self.image:
            raise DeploymentError('Selected service image differs at the requested/synchronized GitOps revision')

    def refresh_protected_main(self):
        # Argo can observe later reviewed merges while this job waits for rollout.
        # Fetch only the approved main branch, never a revision supplied by Argo.
        if self.git('remote', 'get-url', 'origin') != REPOSITORY:
            raise DeploymentError('Git origin does not match the approved GitOps repository')
        self.git('fetch', '--no-tags', 'origin', 'refs/heads/main:refs/remotes/origin/main')
        tip = self.git('rev-parse', '--verify', 'refs/remotes/origin/main^{commit}')
        if not FULL_SHA.fullmatch(tip):
            raise DeploymentError('A full protected origin/main revision is required')
        self.ancestor(self.protected_tip, tip)
        self.ancestor(self.requested, tip)
        self.protected_tip = tip

    def validate_revision(self, revision):
        if not isinstance(revision, str) or not FULL_SHA.fullmatch(revision):
            raise DeploymentError('Synced Argo status must contain a full Git commit SHA')
        try:
            known = self.git('rev-parse', '--verify', revision + '^{commit}') == revision
        except DeploymentError:
            known = False
        if not known or not self.is_ancestor(revision, self.protected_tip):
            self.refresh_protected_main()
        # Both relationships matter: another reviewed service may advance main,
        # but an older, divergent, unknown or unprotected revision cannot pass.
        self.ancestor(revision, self.protected_tip)
        if revision != self.requested and self.is_ancestor(revision, self.requested):
            # Argo may still report the previous healthy revision while its
            # next reconciliation is pending. It cannot satisfy this gate.
            return None
        self.ancestor(self.requested, revision)
        self.match_image(revision)
        from helm_release import selected_chart
        if selected_chart(self.root,self.service,self.environment,revision)!=self.requested_chart:
            raise DeploymentError("Selected Helm chart changed after the requested GitOps revision")
        return revision


def approved_source(source, environment):
    # Use the approved source/value-file contract.
    from helm_release import approved_argo_source
    try:approved_argo_source(source,environment)
    except ValueError as error:raise DeploymentError(str(error)) from error


def application_revision(application, environment, history):
    if application is None:
        return None  # GET --ignore-not-found: bootstrap has not connected this Application yet.
    if not isinstance(application, dict):
        raise DeploymentError('Argo response is not a native Application object')
    metadata, spec, status = application.get('metadata', {}), application.get('spec', {}), application.get('status', {})
    if application.get('kind') != 'Application' or metadata.get('name') != 'boutique-' + environment or metadata.get('namespace') != 'argocd':
        raise DeploymentError('Argo report does not identify the expected environment Application')
    if metadata.get('deletionTimestamp') or spec.get('sources'):
        raise DeploymentError('Deleting or multi-source Applications cannot be verified')
    destination = spec.get('destination', {})
    if destination.get('namespace') != 'boutique-' + environment or destination.get('server') != 'https://kubernetes.default.svc':
        raise DeploymentError('Argo destination is not the selected environment on this cluster')
    approved_source(spec.get('source'), environment)
    if not isinstance(status, dict):
        raise DeploymentError('Malformed native Argo status')
    conditions = status.get('conditions', [])
    if not isinstance(conditions, list):
        raise DeploymentError('Malformed Argo conditions')
    for condition in conditions:
        if not isinstance(condition, dict):
            raise DeploymentError('Malformed Argo condition')
        if str(condition.get('type', '')).endswith('Error'):
            raise DeploymentError('Argo controller reported a comparison/specification/sync error')
    operation = status.get('operationState', {})
    if not isinstance(operation, dict):
        raise DeploymentError('Malformed Argo operation status')
    if operation.get('phase') in ('Failed', 'Error', 'Terminating', 'Terminated'):
        raise DeploymentError('Argo synchronization failed or is terminating')
    if operation.get('phase') in ('Running', 'Pending'):
        return None
    sync, health = status.get('sync', {}), status.get('health', {})
    if not isinstance(sync, dict) or not isinstance(health, dict):
        raise DeploymentError('Malformed Argo synchronization/health status')
    if sync.get('status') != 'Synced' or health.get('status') != 'Healthy':
        return None
    compared = sync.get('comparedTo')
    if compared is not None:
        if not isinstance(compared, dict):
            raise DeploymentError('Malformed Argo comparison status')
        approved_source(compared.get('source'), environment)
        if compared['source']['path'] != spec['source']['path']:
            raise DeploymentError('Argo status was measured against a different source profile')
    return history.validate_revision(sync.get('revision'))


def rollout_complete(deployments, environment, service, image, *, progressive_frontend=False, progressive_services=()):
    if not isinstance(deployments, dict) or deployments.get('kind') not in ('List', 'DeploymentList') or not isinstance(deployments.get('items'), list):
        raise DeploymentError('Expected a native Deployment collection')
    ready = True
    for name in SERVICES:
        matches = [item for item in deployments['items'] if isinstance(item, dict) and item.get('metadata', {}).get('name') == name]
        if len(matches) != 1:
            raise DeploymentError('Every application service requires exactly one measured Deployment')
        deployment = matches[0]
        metadata, spec, status = deployment.get('metadata', {}), deployment.get('spec', {}), deployment.get('status', {})
        if deployment.get('kind') != 'Deployment' or metadata.get('namespace') != 'boutique-' + environment or metadata.get('deletionTimestamp'):
            raise DeploymentError('Deployment belongs to the wrong environment or is deleting')
        if not isinstance(status, dict):
            raise DeploymentError('Malformed native Deployment status')
        containers = spec.get('template', {}).get('spec', {}).get('containers', [])
        matches = [container for container in containers if isinstance(container, dict) and container.get('name') == name]
        if len(matches) != 1:
            raise DeploymentError('Service Deployment must contain its expected application container')
        expected_image = matches[0].get('image', '')
        if not isinstance(expected_image, str) or not re.fullmatch(rf'ghcr\.io/subhankar12-spec/boutique-{name}@sha256:[a-f0-9]{{64}}', expected_image):
            raise DeploymentError('A running application Deployment still selects a mutable or invalid image')
        if name == service and expected_image != image:
            ready = False
        if name in progressive_services or (name == 'frontend' and progressive_frontend):
            continue  # Active Rollout ReplicaSet and Service are verified separately.
        desired, generation = spec.get('replicas', 1), metadata.get('generation')
        if type(desired) is not int or desired < 1 or type(generation) is not int or generation < 1:
            raise DeploymentError('Invalid desired replica count or Deployment generation')
        observed = status.get('observedGeneration', 0)
        if type(observed) is not int or observed < generation:
            ready = False
        for field in ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas'):
            if type(status.get(field)) is not int or status[field] != desired:
                ready = False
        conditions = status.get('conditions', [])
        if not isinstance(conditions, list) or any(not isinstance(condition, dict) for condition in conditions):
            raise DeploymentError('Malformed Deployment conditions')
        condition_map = {condition.get('type'): condition for condition in conditions}
        progress = condition_map.get('Progressing', {})
        if progress.get('status') == 'False' and progress.get('reason') == 'ProgressDeadlineExceeded':
            raise DeploymentError('A service Deployment exceeded its progress deadline')
        if progress.get('status') != 'True' or condition_map.get('Available', {}).get('status') != 'True':
            ready = False
    return ready


def native_json(namespace, resource, *, timeout):
    request_timeout = max(1, min(25, int(timeout)))
    args = ['kubectl', '--request-timeout=' + str(request_timeout) + 's', '-n', namespace,
            'get', *resource, '-o', 'json']
    if namespace == 'argocd' and resource[0] == 'application':
        args.append('--ignore-not-found')
    body = command(args, timeout=request_timeout + 3)
    if not body.strip() and namespace == 'argocd' and resource[0] == 'application':
        return None
    if len(body.encode()) > 4 * 1024 * 1024:
        raise DeploymentError('Native Kubernetes response exceeds the expected size')
    try:
        result = json.loads(body)
    except (ValueError, UnicodeDecodeError) as error:
        raise DeploymentError('Native Kubernetes response is invalid JSON') from error
    if not isinstance(result, dict):
        raise DeploymentError('Native Kubernetes response is not an object')
    return result


def progressive_services(history, application, revision):
    source = application.get('spec', {}).get('source', {})
    approved_source(source, history.environment)
    enabled = False
    backends = False
    for relative in source.get('helm', {}).get('valueFiles', []):
        path = posixpath.normpath('environments/' + history.environment + '/' + relative)
        document = yaml.safe_load(history.git('show', revision + ':' + path))
        if not isinstance(document, dict):
            raise DeploymentError('Trusted Helm values must be an object')
        if 'progressiveDelivery' in document:
            config = document['progressiveDelivery']
            if not isinstance(config, dict) or type(config.get('enabled')) is not bool:
                raise DeploymentError('Progressive delivery requires an explicit boolean switch')
            enabled = config['enabled']
            if 'backendsEnabled' in config:
                if type(config['backendsEnabled']) is not bool:
                    raise DeploymentError('Backend delivery requires a boolean switch')
                backends = config['backendsEnabled']
    if backends and not enabled:
        raise DeploymentError('Backend delivery requires progressive delivery')
    return SERVICES if enabled and backends else ('frontend',) if enabled else ()


def progressive_enabled(history, application, revision):
    return bool(progressive_services(history, application, revision))


def verify_progressive(history, deployments, read, deadline, clock, name="frontend"):
    namespace = 'boutique-' + history.environment
    request = lambda resource: read(namespace, resource, timeout=max(1, deadline-clock()))
    deployment = next(item for item in deployments['items'] if item.get('metadata', {}).get('name') == name)
    image = next(container['image'] for container in deployment['spec']['template']['spec']['containers'] if container['name'] == name)
    rollout = request(['rollout.argoproj.io', name])
    replicasets = request(['replicasets'])
    service = request(['service', name])
    try:
        if not progressive_ready(deployment, rollout, replicasets, service, history.environment, image, name):
            return False
        # Recheck controller state after measuring the actual serving resources.
        return progressive_ready(deployment, request(['rollout.argoproj.io', name]), replicasets, service, history.environment, image, name)
    except ProgressiveError as error:
        raise DeploymentError(str(error)) from error


def wait_for_deployment(history, timeout, *, read=native_json, clock=time.monotonic, sleep=time.sleep):
    if type(timeout) is not int or not 1 <= timeout <= 1800:
        raise DeploymentError('Timeout must be between 1 and 1800 seconds')
    deadline = clock() + timeout
    while clock() < deadline:
        application = read('argocd', ['application', 'boutique-' + history.environment], timeout=max(1, deadline-clock()))
        revision = application_revision(application, history.environment, history)
        if (not revision and application
                and application.get('status', {}).get('health', {}).get('status') == 'Degraded'
                and application.get('status', {}).get('operationState', {}).get('phase') not in ('Running', 'Pending')):
            # A recovered stable version must not bless the attempted release.
            candidate_revision = application.get('status', {}).get('sync', {}).get('revision', '')
            if FULL_SHA.fullmatch(candidate_revision) and history.validate_revision(candidate_revision) and progressive_enabled(history, application, candidate_revision):
                for name in progressive_services(history, application, candidate_revision):
                    rollout = read('boutique-' + history.environment, ['rollout.argoproj.io', name], timeout=max(1, deadline-clock()))
                    if rollout.get('status', {}).get('abort') is True or rollout.get('status', {}).get('phase') == 'Degraded':
                        raise DeploymentError('Progressive release aborted; stable traffic recovered but promotion is blocked')
        if revision:
            history.progressive_services = progressive_services(history, application, revision)
            history.progressive_frontend = 'frontend' in history.progressive_services
            deployments = read('boutique-' + history.environment, ['deployments'], timeout=max(1, deadline-clock()))
            if rollout_complete(deployments, history.environment, history.service, history.image, progressive_services=history.progressive_services) and all(verify_progressive(history, deployments, read, deadline, clock, name) for name in history.progressive_services):
                # Check Argo again after workload measurements. A concurrent sync
                # must settle and be bound to its actual revision before returning.
                final = read('argocd', ['application', 'boutique-' + history.environment], timeout=max(1, deadline-clock()))
                final_revision = application_revision(final, history.environment, history)
                if final_revision == revision and clock() < deadline:
                    return revision
        remaining = deadline-clock()
        if remaining <= 0:
            break
        sleep(min(5, remaining))
    raise DeploymentError('Timed out waiting for the approved Argo revision and completed service rollouts')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment', choices=ENVIRONMENTS, required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--service', choices=SERVICES, required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--gitops-root', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=480)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--workloads-output', type=Path, help='Verified workload kinds for downstream read-only reporting')
    args = parser.parse_args()
    try:
        history = TrustedHistory(args.gitops_root, args.environment, args.service, args.image, args.commit)
        revision = wait_for_deployment(history, args.timeout)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            temporary = args.output.with_name(args.output.name + '.pending')
            temporary.write_text(revision + '\n')
            temporary.replace(args.output)
        if args.workloads_output:
            args.workloads_output.parent.mkdir(parents=True, exist_ok=True)
            args.workloads_output.write_text(json.dumps({name: 'Rollout' if name in history.progressive_services else 'Deployment' for name in SERVICES}) + '\n')
        print(revision)
        return 0
    except (DeploymentError, ValueError, TypeError, KeyError, AttributeError, OSError) as error:
        print('Verification blocked: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
