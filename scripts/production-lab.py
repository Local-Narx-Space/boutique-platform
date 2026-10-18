#!/usr/bin/env python3
"""Bootstrap and verify two isolated production-learning clusters without cloud spend.

Every Kubernetes operation supplies an explicit context. Secret material is read
from private files/in memory, never put in process arguments or printed.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import time
import urllib.request

PLATFORM = Path(__file__).resolve().parents[1]
CONFIG = PLATFORM / 'local/production-lab'
GITOPS = PLATFORM.parent / 'boutique-gitops'
STATE = CONFIG / '.state'
CACHE = CONFIG / '.cache'
LOCK = json.loads((CONFIG / 'versions.lock.json').read_text())
CLUSTERS = {'nonprod': ['dev', 'staging'], 'production': ['production']}
SERVICES = ('frontend', 'catalogue', 'cart', 'orders')
DIGEST_IMAGE = re.compile(r'^ghcr\.io/[a-zA-Z0-9_.-]+/boutique-[a-z-]+@sha256:[0-9a-f]{64}$')


def run(args, *, input=None, capture=True, timeout=120, binary=False):
    """Do not render commands/inputs on failure: inputs may contain credentials."""
    result = subprocess.run([str(x) for x in args], input=input, capture_output=capture,
                            text=not binary, timeout=timeout)
    if result.returncode:
        # Errors may contain URL credentials when a tool echoes request data.
        raise RuntimeError(f'{Path(str(args[0])).name} failed (exit {result.returncode}); inspect the scoped command locally')
    return result.stdout if capture else ''


def kind_command():
    cached = CACHE / 'bin/kind'
    return str(cached) if cached.is_file() else 'kind'


def context(cluster):
    if cluster not in CLUSTERS:
        raise ValueError('Cluster must be nonprod or production')
    return f'kind-boutique-{cluster}'


def environment_cluster(environment):
    for cluster, environments in CLUSTERS.items():
        if environment in environments:
            return cluster
    raise ValueError('Environment must be dev, staging or production')


def kube(cluster, *args, **kwargs):
    return run(['kubectl', '--context', context(cluster), *args], **kwargs)


def apply(cluster, objects, namespace=None):
    args = ['apply', '--server-side', '--field-manager=boutique-lab-bootstrap', '-f', '-']
    if namespace:
        args += ['-n', namespace]
    text = objects if isinstance(objects, str) else json.dumps(objects)
    kube(cluster, *args, input=text)


def namespace(cluster, name, restricted=True):
    labels = {'pod-security.kubernetes.io/enforce': 'restricted',
              'pod-security.kubernetes.io/audit': 'restricted',
              'pod-security.kubernetes.io/warn': 'restricted'} if restricted else {}
    apply(cluster, {'apiVersion': 'v1', 'kind': 'Namespace',
                    'metadata': {'name': name, 'labels': labels}})


def private_dir(path):
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError('Private state directory cannot be a symlink')
    path.chmod(0o700)


def read_private(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError('Credential files must be regular private files (chmod 600)')
    value = path.read_text().strip()
    if not value:
        raise ValueError('Credential file is empty')
    return value


def download_verified(entry, destination):
    """A cached artifact is checked on every use, not just first download."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        if hashlib.sha256(destination.read_bytes()).hexdigest() == entry['sha256']:
            return destination
        raise ValueError(f'Cached artifact checksum mismatch: {destination.name}')
    with urllib.request.urlopen(entry['url'], timeout=60) as response:
        payload = response.read(25 * 1024 * 1024 + 1)
    if len(payload) > 25 * 1024 * 1024 or hashlib.sha256(payload).hexdigest() != entry['sha256']:
        raise ValueError(f'Artifact checksum mismatch: {destination.name}')
    temporary = destination.with_suffix('.download')
    temporary.write_bytes(payload)
    os.replace(temporary, destination)
    return destination


def pinned_manifest(name):
    file = download_verified(LOCK['manifests'][name], CACHE / f'{name}.yaml')
    content = file.read_text()
    # Freeze every upstream image tag. The lock intentionally uses Docker Hub's
    # official Redis mirror for the ECR Redis image in Argo's published manifest.
    def replace(match):
        image = match.group(2).strip('"\'')
        if image not in LOCK['controller_images']:
            raise ValueError(f'Controller image missing from lock: {image}')
        return match.group(1) + LOCK['controller_images'][image]
    return re.sub(r'(?m)^(\s*image:\s*)([^\n]+)$', replace, content)


def doctor_report():
    blockers, warnings, details = [], [], {}
    for command in ('docker', 'kubectl', 'openssl', 'helm'):
        if not shutil.which(command):
            blockers.append(f'{command} is missing; install the documented tools')
    if not shutil.which(kind_command()) and not Path(kind_command()).is_file():
        blockers.append('kind is missing; run production-lab.py fetch-tools')
    if 'docker is missing; install the documented tools' not in blockers:
        try:
            info = json.loads(run(['docker', 'info', '--format', '{{json .}}'], timeout=20))
            root = Path(info['DockerRootDir'])
            details.update(driver=info['Driver'], docker_root=str(root),
                           memory_gib=round(info['MemTotal'] / 1024**3, 1),
                           cgroup_driver=info.get('CgroupDriver'))
            free = shutil.disk_usage(root).free / 1024**3
            details['free_disk_gib'] = round(free, 1)
            if free < 30:
                blockers.append('Docker storage needs at least 30 GiB free for two clusters; free unused caches or use a larger lab host')
            if info['MemTotal'] < 16 * 1024**3:
                blockers.append('At least 16 GiB RAM is required; 24–32 GiB is recommended')
            if info['Driver'] == 'vfs':
                warnings.append('VFS duplicates image layers; allow at least 60 GiB free or use overlay2 on a supported host')
        except (RuntimeError, KeyError, json.JSONDecodeError, OSError):
            blockers.append('Docker daemon or its storage is inaccessible')
    try:
        version = run([kind_command(), 'version'], timeout=15)
        details['kind'] = version.strip()
        if not re.search(r'kind v0\.33\.0\b', version):
            blockers.append('The lab locks kind 0.33.0; run production-lab.py fetch-tools')
    except (RuntimeError, FileNotFoundError):
        pass
    if Path('/sys/fs/cgroup').exists() and not os.access('/sys/fs/cgroup', os.W_OK):
        warnings.append('Runner cgroups are read-only. Nested kubelet may fail; use a Linux VM/bare-metal Docker host if cluster creation fails')
    for port in (8443, 9443):
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', port))
            except OSError:
                warnings.append(f'Loopback port {port} is occupied; an existing lab cluster may own it')
    return {'ready': not blockers, 'blockers': blockers, 'warnings': warnings, 'details': details}


def doctor(as_json=False):
    report = doctor_report()
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print('Preflight: ' + ('READY' if report['ready'] else 'BLOCKED'))
        for category in ('blockers', 'warnings'):
            for issue in report[category]:
                print(f'{category[:-1].upper()}: {issue}')
        print(json.dumps(report['details'], indent=2))
    return report['ready']


def fetch_tools():
    if os.uname().machine != 'x86_64' or sys.platform != 'linux':
        raise ValueError('This artifact lock is for Linux amd64; add and review another architecture lock first')
    executable = download_verified(LOCK['kind'], CACHE / 'bin/kind')
    executable.chmod(0o755)
    print(run([executable, 'version']).strip())


def ensure_ca(cluster):
    folder = STATE / cluster / 'ca'
    private_dir(folder)
    key, cert = folder / 'ca.key', folder / 'ca.crt'
    if key.exists() != cert.exists():
        raise ValueError('Partial CA state; restore the original pair rather than replacing cluster trust')
    if not key.exists():
        run(['openssl', 'req', '-x509', '-newkey', 'rsa:3072', '-nodes', '-days', '365',
             '-subj', f'/CN=Boutique {cluster} lab CA', '-addext', 'basicConstraints=critical,CA:TRUE',
             '-addext', 'keyUsage=critical,keyCertSign,cRLSign', '-keyout', key, '-out', cert])
        key.chmod(0o600)
        cert.chmod(0o644)
    run(['openssl', 'x509', '-in', cert, '-checkend', '86400', '-noout'])
    return key, cert


def secret(cluster, ns, name, values, *, secret_type='Opaque', preserve=True):
    current = kube(cluster, '-n', ns, 'get', 'secret', name, '--ignore-not-found', '-o', 'json', timeout=30)
    if current.strip() and preserve:
        print(f'Preserved secret {ns}/{name}')
        return False
    # base64 avoids kubectl emitting stringData on ownership-conflict errors.
    data = {k: base64.b64encode(v.encode()).decode() for k, v in values.items()}
    apply(cluster, {'apiVersion': 'v1', 'kind': 'Secret',
                    'metadata': {'name': name, 'namespace': ns}, 'type': secret_type, 'data': data})
    print(f'Created secret {ns}/{name}')
    return True


def bootstrap(cluster):
    report = doctor_report()
    if not report['ready']:
        doctor()
        raise ValueError('Preflight must pass before cluster creation; no images have been pulled')
    private_dir(STATE / cluster)
    clusters = run([kind_command(), 'get', 'clusters']).splitlines()
    name = f'boutique-{cluster}'
    if name not in clusters:
        print(f'Creating isolated {cluster} cluster; this can take several minutes', flush=True)
        try:
            # Node Ready requires a CNI. Install Calico first, then enforce
            # node/DaemonSet readiness below; kind's pre-CNI wait cannot pass.
            run([kind_command(), 'create', 'cluster', '--name', name, '--image', LOCK['node_image'],
                 '--config', CONFIG / f'kind-{cluster}.yaml', '--kubeconfig', STATE / cluster / 'kubeconfig',
                 '--wait', '0s'], timeout=360, capture=False)
        except (RuntimeError, subprocess.TimeoutExpired):
            # Preserve any created nodes for diagnostics, never destroy user data.
            logs = STATE / cluster / 'kind-failure-logs'
            subprocess.run([kind_command(), 'export', 'logs', str(logs), '--name', name], capture_output=True)
            raise RuntimeError(f'Cluster creation failed. Scoped logs: {logs}; check cgroups/kernel modules. No cleanup or success is assumed')
    # Export after every bootstrap; it supplies a dedicated Jenkins credential file.
    run([kind_command(), 'export', 'kubeconfig', '--name', name, '--kubeconfig', STATE / cluster / 'kubeconfig'])
    (STATE / cluster / 'kubeconfig').chmod(0o600)
    # kind --kubeconfig does not populate the default kubeconfig; explicitly use it.
    os.environ['KUBECONFIG'] = str(STATE / cluster / 'kubeconfig')
    cidr = '192.168.0.0/16' if cluster == 'nonprod' else '172.20.0.0/16'
    calico = pinned_manifest('calico').replace('# - name: CALICO_IPV4POOL_CIDR', '- name: CALICO_IPV4POOL_CIDR').replace('#   value: "192.168.0.0/16"', f'  value: "{cidr}"')
    apply(cluster, calico)
    kube(cluster, '-n', 'kube-system', 'rollout', 'status', 'daemonset/calico-node', '--timeout=300s', timeout=330)
    kube(cluster, 'wait', '--for=condition=Ready', 'nodes', '--all', '--timeout=300s', timeout=330)
    for name in ('cert-manager', 'argocd', 'traefik'):
        # Upstream controllers need cluster service-account access and their
        # published security settings; workload namespaces enforce Restricted.
        namespace(cluster, name, restricted=False)
        apply(cluster, pinned_manifest(name), namespace=name)
    kube(cluster, '-n', 'cert-manager', 'rollout', 'status', 'deployment/cert-manager-webhook', '--timeout=300s', timeout=330)
    kube(cluster, 'wait', '--for=condition=Established', 'crd/certificates.cert-manager.io', '--timeout=120s', timeout=140)
    # The published chart renders LoadBalancer; the lab exposes only loopback TLS.
    patch = {'spec': {'type': 'NodePort', 'ports': [
        {'name': 'web', 'port': 80, 'targetPort': 'web', 'nodePort': 30080},
        {'name': 'websecure', 'port': 443, 'targetPort': 'websecure', 'nodePort': 30443}]}}
    kube(cluster, '-n', 'traefik', 'patch', 'service', 'traefik', '--type=strategic', '-p', json.dumps(patch))
    patch = {'spec': {'replicas': 2, 'template': {'spec': {'containers': [{
        'name': 'traefik', 'args': ['--entryPoints.web.address=:8000/tcp', '--entryPoints.websecure.address=:8443/tcp',
                                  '--entryPoints.traefik.address=:8080/tcp', '--ping=true', '--providers.kubernetesingress',
                                  '--providers.kubernetesingress.ingressclass=traefik', '--entryPoints.websecure.http.tls=true',
                                  '--entryPoints.web.http.redirections.entrypoint.to=websecure',
                                  '--entryPoints.web.http.redirections.entrypoint.scheme=https', '--log.format=json', '--accesslog=true', '--accesslog.format=json'],
        'resources': {'requests': {'cpu': '100m', 'memory': '64Mi'}, 'limits': {'cpu': '500m', 'memory': '256Mi'}}}],
        'topologySpreadConstraints': [{'maxSkew': 1, 'topologyKey': 'kubernetes.io/hostname', 'whenUnsatisfiable': 'DoNotSchedule',
                                      'labelSelector': {'matchLabels': {'app.kubernetes.io/name': 'traefik'}}}]}}}}
    kube(cluster, '-n', 'traefik', 'patch', 'deployment', 'traefik', '--type=strategic', '-p', json.dumps(patch))
    key, cert = ensure_ca(cluster)
    existing_ca = kube(cluster, '-n', 'cert-manager', 'get', 'secret', 'boutique-lab-root-ca', '--ignore-not-found', '-o', 'json', timeout=30)
    if existing_ca.strip():
        existing_certificate = base64.b64decode(json.loads(existing_ca)['data']['tls.crt']).decode()
        if existing_certificate != cert.read_text():
            raise ValueError('Existing cluster CA differs from local private state; restore the original CA files before continuing')
    secret(cluster, 'cert-manager', 'boutique-lab-root-ca', {'tls.key': key.read_text(), 'tls.crt': cert.read_text()}, secret_type='kubernetes.io/tls')
    apply(cluster, {'apiVersion': 'cert-manager.io/v1', 'kind': 'ClusterIssuer',
                    'metadata': {'name': 'boutique-lab-ca'}, 'spec': {'ca': {'secretName': 'boutique-lab-root-ca'}}})
    kube(cluster, 'wait', '--for=condition=Ready', 'clusterissuer/boutique-lab-ca', '--timeout=120s', timeout=140)
    for name in ('traefik', 'argocd'):
        kube(cluster, '-n', name, 'rollout', 'status', f'deployment/{"argocd-server" if name == "argocd" else name}', '--timeout=300s', timeout=330)
    print(f'{cluster} foundation is ready. Application delivery remains a separate verified step.')


def cluster_state(cluster):
    config = STATE / cluster / 'kubeconfig'
    if not config.exists():
        raise ValueError('Dedicated kubeconfig missing; bootstrap this cluster first')
    os.environ['KUBECONFIG'] = str(config)
    kube(cluster, 'get', '--raw=/readyz', timeout=20)


def configure_secrets(cluster, repo_token_file, registry_token_file, registry_user):
    cluster_state(cluster)
    repo_token = read_private(repo_token_file) if repo_token_file else None
    registry_token = read_private(registry_token_file)
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', registry_user):
        raise ValueError('Invalid registry user')
    if repo_token:
        secret(cluster, 'argocd', 'boutique-gitops-repository', {
            'url': 'https://github.com/Local-Narx-Space/boutique-gitops.git', 'username': 'git', 'password': repo_token})
        kube(cluster, '-n', 'argocd', 'label', 'secret', 'boutique-gitops-repository',
             'argocd.argoproj.io/secret-type=repository', '--overwrite')
    auth = base64.b64encode(f'{registry_user}:{registry_token}'.encode()).decode()
    config = json.dumps({'auths': {'ghcr.io': {'auth': auth}}})
    _, ca = ensure_ca(cluster)
    for env in CLUSTERS[cluster]:
        ns = 'boutique-' + env
        namespace(cluster, ns)
        secret(cluster, ns, 'ghcr-pull', {'.dockerconfigjson': config}, secret_type='kubernetes.io/dockerconfigjson')
        secret(cluster, ns, 'boutique-data-ca', {'ca.crt': ca.read_text()})
        current = json.loads(kube(cluster, '-n', ns, 'get', 'secrets', '-o', 'json'))
        names = {x['metadata']['name'] for x in current['items']}
        if ('redis-auth' in names) != ('cart-redis' in names):
            raise ValueError(f'Partial Redis credential state in {ns}; restore matching credentials first')
        redis_password = secrets.token_hex(32)
        secret(cluster, ns, 'redis-auth', {'password': redis_password})
        secret(cluster, ns, 'cart-redis', {'url': f'rediss://:{redis_password}@redis:6379/0?ssl_ca_certs=/run/data-ca/ca.crt'})
        secret(cluster, ns, 'frontend-session', {'secret': secrets.token_hex(32)})
        secret(cluster, ns, 'orders-database', {'url': 'jdbc:postgresql://postgres:5432/boutique?sslmode=verify-full&sslrootcert=/run/data-ca/ca.crt',
                                              'username': 'boutique', 'password': secrets.token_hex(32)})
    namespace(cluster, 'monitoring')
    secret(cluster, 'monitoring', 'ghcr-pull', {'.dockerconfigjson': config}, secret_type='kubernetes.io/dockerconfigjson')
    # Mock URLs are deliberately local; supplying real endpoints is a reviewed
    # deployment change, not an automatic external message/ticket test.
    secret(cluster, 'monitoring', 'monitoring-integrations', {
        'slack_webhook': 'http://mock-receivers:18080/slack', 'webhook_token': secrets.token_hex(32),
        'servicenow_password': secrets.token_hex(32), 'grafana_password': secrets.token_hex(32)})
    if cluster == 'production':
        password = secrets.token_hex(32)
        secret(cluster, 'monitoring', 'monitoring-incident-queue', {
            'password': password, 'postgresql_ca': ca.read_text(),
            'QUEUE_DATABASE_URL': f'postgresql://incidentqueue:{password}@incident-queue-postgres.monitoring.svc:5432/incidentqueue?sslmode=verify-full&sslrootcert=/run/queue-ca/ca.crt'})
    apply(cluster, {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': 'boutique-mock-receivers', 'namespace': 'monitoring'},
                    'data': {'mock-receivers.py': (PLATFORM / 'monitoring/mock-receivers.py').read_text()}})
    print('Credentials configured without exposing their values. Existing secrets were preserved.')



def export_verifier(environment, duration='24h'):
    cluster = environment_cluster(environment)
    cluster_state(cluster)
    match = re.fullmatch(r'([1-9][0-9]*)(m|h)', duration)
    if not match or int(match.group(1)) * (60 if match.group(2) == 'm' else 3600) > 86400:
        raise ValueError('Verifier token lifetime must be a positive duration up to 24h')
    ns, account = 'boutique-' + environment, 'jenkins-verifier'
    namespace(cluster, ns)
    subject = {'kind': 'ServiceAccount', 'name': account, 'namespace': ns}
    objects = [
        {'apiVersion': 'v1', 'kind': 'ServiceAccount', 'metadata': {'name': account, 'namespace': ns}, 'automountServiceAccountToken': False},
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'Role', 'metadata': {'name': account, 'namespace': ns}, 'rules': [
            {'apiGroups': [''], 'resources': ['pods', 'services', 'events'], 'verbs': ['get', 'list', 'watch']},
            {'apiGroups': ['apps'], 'resources': ['deployments', 'replicasets', 'statefulsets'], 'verbs': ['get', 'list', 'watch']}]},
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'RoleBinding', 'metadata': {'name': account, 'namespace': ns},
         'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': account}, 'subjects': [subject]},
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'Role', 'metadata': {'name': account + '-' + environment, 'namespace': 'argocd'},
         'rules': [{'apiGroups': ['argoproj.io'], 'resources': ['applications'], 'verbs': ['get', 'list', 'watch']}]},
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'RoleBinding', 'metadata': {'name': account + '-' + environment, 'namespace': 'argocd'},
         'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': account + '-' + environment}, 'subjects': [subject]}]
    apply(cluster, {'apiVersion': 'v1', 'kind': 'List', 'items': objects})
    token = kube(cluster, '-n', ns, 'create', 'token', account, '--duration=' + duration).strip()
    admin = json.loads(kube(cluster, 'config', 'view', '--raw', '--minify', '-o', 'json'))
    connection = admin['clusters'][0]['cluster']
    if 'certificate-authority-data' not in connection or connection.get('insecure-skip-tls-verify'):
        raise ValueError('Cluster configuration lacks verified API trust')
    document = {'apiVersion': 'v1', 'kind': 'Config', 'clusters': [{'name': cluster, 'cluster': connection}],
                'contexts': [{'name': environment, 'context': {'cluster': cluster, 'user': account, 'namespace': ns}}],
                'current-context': environment, 'users': [{'name': account, 'user': {'token': token}}]}
    destination = STATE / cluster / ('jenkins-verifier-' + environment + '.json')
    destination.write_text(json.dumps(document)); destination.chmod(0o600)
    print(f'Created read-only verifier credential: {destination}. Requested expiry: {duration}; renew before expiry. Never upload the operator kubeconfig to Jenkins.')


def validate_release_images(rendered, *, monitoring=False):
    images = re.findall(r'^\s*image:\s*([^\s]+)', rendered, re.M)
    applications = [i.strip('"\'') for i in images if 'ghcr.io/' in i and 'boutique-' in i]
    expected = len(applications) if monitoring and len(applications) in (0, 1) else (1 if monitoring else len(SERVICES))
    if len(applications) != expected or any(not DIGEST_IMAGE.fullmatch(i) for i in applications):
        raise ValueError('Every Boutique image must be a published immutable GHCR digest. Complete release/promotion first; bootstrap/local tags are rejected')
    return applications


def argo_application(cluster, env, *, monitoring=False):
    name = f'boutique-{env}' if not monitoring else f'lab-monitoring-{cluster}'
    destination = 'boutique-' + env if not monitoring else 'monitoring'
    path = f'environments/{env}' if not monitoring else 'monitoring'
    namespaces = [destination] if not monitoring else ['monitoring', 'monitoring-logs'] + ['boutique-'+environment for environment in CLUSTERS[cluster]]
    # Monitoring discovery uses Roles in the selected application namespaces;
    # the chart requires no cluster-wide RBAC grants.
    cluster_resources = [{'group': '', 'kind': 'Namespace'}]
    project = {'apiVersion': 'argoproj.io/v1alpha1', 'kind': 'AppProject', 'metadata': {'name': name, 'namespace': 'argocd'},
               'spec': {'sourceRepos': ['https://github.com/Local-Narx-Space/boutique-gitops.git'],
                        'destinations': [{'namespace': ns, 'server': 'https://kubernetes.default.svc'} for ns in namespaces],
                        'clusterResourceWhitelist': cluster_resources, 'namespaceResourceWhitelist': [{'group': '*', 'kind': '*'}]}}
    app = {'apiVersion': 'argoproj.io/v1alpha1', 'kind': 'Application', 'metadata': {'name': name, 'namespace': 'argocd'},
           'spec': {'project': name, 'source': {'repoURL': 'https://github.com/Local-Narx-Space/boutique-gitops.git', 'targetRevision': 'main', 'path': path},
                    'destination': {'server': 'https://kubernetes.default.svc', 'namespace': destination},
                    'syncPolicy': {'automated': {'prune': True, 'selfHeal': True}, 'syncOptions': ['CreateNamespace=true'],
                                   'retry': {'limit': 3, 'backoff': {'duration': '5s', 'factor': 2, 'maxDuration': '1m'}}}}}
    if monitoring:
        app["spec"]["source"]["helm"]={"releaseName":"lab-monitoring-"+cluster,"valueFiles":[f"profiles/{cluster}/values.yaml",f"../lab-profiles/monitoring/{cluster}/values.yaml"]}
    else:
        app["spec"]["source"]["helm"]={"releaseName":"boutique-"+env,"valueFiles":["values.yaml","releases.yaml",f"../../lab-profiles/{env}/values.yaml"]}
    return project, app


def selected_environments(cluster, environment=None, monitoring=False):
    if cluster not in CLUSTERS:
        raise ValueError('Invalid cluster')
    if monitoring and environment:
        raise ValueError('Monitoring profiles are selected by cluster; do not supply an application environment')
    if environment is not None and environment not in CLUSTERS[cluster]:
        raise ValueError('Selected environment does not belong to this cluster')
    return [environment] if environment else CLUSTERS[cluster]


def deploy(cluster, monitoring=False, environment=None):
    selected = selected_environments(cluster, environment, monitoring)
    cluster_state(cluster)
    # Validate all selected profiles before mutating any Applications.
    environments = ['production' if cluster == 'production' else 'dev'] if monitoring else selected
    for env in environments:
        rendered = run(['python3',GITOPS/'scripts/render-monitoring.py',cluster,'--lab']) if monitoring else run(['python3',GITOPS/'scripts/render.py',env,'--profile','lab'])
        validate_release_images(rendered, monitoring=monitoring)
    for env in environments:
        project, app = argo_application(cluster, env, monitoring=monitoring)
        apply(cluster, {'apiVersion': 'v1', 'kind': 'List', 'items': [project, app]})
        print(f'Configured {app["metadata"]["name"]}; Argo CD must fetch the published main branch. Run readiness next.')


def readiness(cluster, monitoring=False, environment=None):
    selected = selected_environments(cluster, environment, monitoring)
    cluster_state(cluster)
    kube(cluster, 'wait', '--for=condition=Ready', 'nodes', '--all', '--timeout=120s', timeout=140)
    kube(cluster, '-n', 'kube-system', 'rollout', 'status', 'daemonset/calico-node', '--timeout=120s', timeout=140)
    applications = [f'lab-monitoring-{cluster}'] if monitoring else [f'boutique-{env}' for env in selected]
    for name in applications:
        app = json.loads(kube(cluster, '-n', 'argocd', 'get', 'application', name, '-o', 'json'))
        status = app.get('status', {})
        if status.get('sync', {}).get('status') != 'Synced' or status.get('health', {}).get('status') != 'Healthy':
            raise ValueError(f'{name} is not Synced and Healthy; inspect its Argo conditions/resources')
    if monitoring:
        for ns in ('monitoring', 'monitoring-logs'):
            workloads = json.loads(kube(cluster, '-n', ns, 'get', 'deployments,statefulsets,daemonsets', '-o', 'json'))
            for workload in workloads['items']:
                resource = workload['kind'].lower() + '/' + workload['metadata']['name']
                kube(cluster, '-n', ns, 'rollout', 'status', resource, '--timeout=240s', timeout=260)
        claims = json.loads(kube(cluster, '-n', 'monitoring', 'get', 'persistentvolumeclaims', '-o', 'json'))
        if any(claim.get('status', {}).get('phase') != 'Bound' for claim in claims['items']):
            raise ValueError('Monitoring persistence is not Bound')
        if cluster == 'production':
            certificate = kube(cluster, '-n', 'monitoring', 'get', 'certificate', 'incident-queue-tls', '--ignore-not-found', '-o', 'name')
            if certificate.strip():
                kube(cluster, '-n', 'monitoring', 'wait', '--for=condition=Ready', 'certificate/incident-queue-tls', '--timeout=120s', timeout=140)
        print('PASS: monitoring Argo sync/health, component rollouts, persistent volumes and optional production queue certificate')
        return
    for env in selected:
        ns = 'boutique-' + env
        kube(cluster, '-n', ns, 'wait', '--for=condition=Ready', 'certificate/frontend-tls', 'certificate/postgres-tls', 'certificate/redis-tls', '--timeout=120s', timeout=140)
        for svc in SERVICES:
            kube(cluster, '-n', ns, 'rollout', 'status', 'deployment/' + svc, '--timeout=240s', timeout=260)
        deployments = json.loads(kube(cluster, '-n', ns, 'get', 'deployments', '-o', 'json'))
        for deployment in deployments['items']:
            for container in deployment['spec']['template']['spec']['containers']:
                if deployment['metadata']['name'] in SERVICES and not DIGEST_IMAGE.fullmatch(container['image']):
                    raise ValueError('Running service image is not an immutable release digest')
    print('PASS: nodes, policy controller, Argo sync/health, workload rollout, data/frontend certificates and immutable service images')


def verify(environment):
    cluster = environment_cluster(environment)
    cluster_state(cluster)
    host = environment + '.boutique.test'
    port = 9443 if cluster == 'production' else 8443
    if '127.0.0.1' not in {a[4][0] for a in socket.getaddrinfo(host, port, socket.AF_INET)}:
        raise ValueError(f'Add 127.0.0.1 {host} to your local hosts file; TLS hostname validation remains enabled')
    ca = STATE / cluster / 'ca/ca.crt'
    env = os.environ.copy()
    env.update(BASE_URL=f'https://{host}:{port}', SSL_CERT_FILE=str(ca), NO_PROXY=host + ',127.0.0.1,localhost', no_proxy=host + ',127.0.0.1,localhost')
    subprocess.run([sys.executable, str(PLATFORM / 'tests/smoke/smoke.py')], env=env, check=True, timeout=210)
    print('PASS: functional smoke through TLS ingress with CA and hostname verification')


def probe_job(namespace_name, name, address, blocked):
    condition = f'if curl -fsS --connect-timeout 3 --max-time 5 http://{address}:8080/health/live >/dev/null; then exit 1; fi' if blocked else f'curl -fsS --connect-timeout 3 --max-time 5 http://{address}:8080/health/live >/dev/null'
    return {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': {'name': name, 'namespace': namespace_name},
            'spec': {'backoffLimit': 0, 'activeDeadlineSeconds': 45, 'ttlSecondsAfterFinished': 300,
                     'template': {'spec': {'restartPolicy': 'Never', 'automountServiceAccountToken': False,
                                          'securityContext': {'runAsNonRoot': True, 'runAsUser': 10001, 'runAsGroup': 10001, 'seccompProfile': {'type': 'RuntimeDefault'}},
                                          'containers': [{'name': 'probe', 'image': LOCK['probe_image'], 'command': ['sh', '-c', condition],
                                                          'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}},
                                                          'resources': {'requests': {'cpu': '10m', 'memory': '16Mi'}, 'limits': {'cpu': '100m', 'memory': '64Mi'}}}]}}}}


def policy_drill(environment):
    cluster = environment_cluster(environment)
    cluster_state(cluster)
    address = json.loads(kube(cluster, '-n', 'boutique-' + environment, 'get', 'service', 'frontend', '-o', 'json'))['spec']['clusterIP']
    if not re.fullmatch(r'[0-9.]+', address):
        raise ValueError('Policy drill requires an IPv4 service IP')
    suffix = secrets.token_hex(4)
    deny_ns, allow_job, deny_job = 'boutique-policy-drill-' + suffix, 'policy-allow-' + suffix, 'policy-deny-' + suffix
    namespace(cluster, deny_ns)
    try:
        apply(cluster, probe_job('traefik', allow_job, address, False))
        kube(cluster, '-n', 'traefik', 'wait', '--for=condition=Complete', 'job/' + allow_job, '--timeout=60s', timeout=75)
        apply(cluster, probe_job(deny_ns, deny_job, address, True))
        kube(cluster, '-n', deny_ns, 'wait', '--for=condition=Complete', 'job/' + deny_job, '--timeout=60s', timeout=75)
        check_job = allow_job + '-check'
        apply(cluster, probe_job('traefik', check_job, address, False))
        kube(cluster, '-n', 'traefik', 'wait', '--for=condition=Complete', 'job/' + check_job, '--timeout=60s', timeout=75)
        print('PASS: permitted ingress namespace connects before/after; unrelated namespace is blocked at the same IPv4 Service endpoint')
    finally:
        # Only disposable objects created by this invocation are removed.
        kube(cluster, '-n', 'traefik', 'delete', 'job', allow_job, allow_job + '-check', '--ignore-not-found')
        kube(cluster, 'delete', 'namespace', deny_ns, '--ignore-not-found', '--wait=false')


def restore_drill(environment):
    cluster = environment_cluster(environment)
    cluster_state(cluster)
    ns, db = 'boutique-' + environment, 'boutique_restore_drill_' + secrets.token_hex(4)
    backup_dir = STATE / cluster / 'backups'
    private_dir(backup_dir)
    backup = backup_dir / (environment + '-' + time.strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(4) + '.dump')
    exec_args = ['-n', ns, 'exec', 'postgres-0', '--']
    query = 'SELECT count(*) FROM orders;'
    before = int(kube(cluster, *exec_args, 'psql', '-U', 'boutique', '-d', 'boutique', '-At', '-c', query).strip())
    dump = kube(cluster, *exec_args, 'pg_dump', '-U', 'boutique', '-d', 'boutique', '-Fc', binary=True)
    backup.write_bytes(dump)
    backup.chmod(0o600)
    after = int(kube(cluster, *exec_args, 'psql', '-U', 'boutique', '-d', 'boutique', '-At', '-c', query).strip())
    started = time.monotonic()
    kube(cluster, *exec_args, 'createdb', '-U', 'boutique', db)
    try:
        kube(cluster, '-n', ns, 'exec', '-i', 'postgres-0', '--', 'pg_restore', '-U', 'boutique', '-d', db, '--exit-on-error', input=dump, binary=True)
        restored = int(kube(cluster, *exec_args, 'psql', '-U', 'boutique', '-d', db, '-At', '-c', query).strip())
        migrations = int(kube(cluster, *exec_args, 'psql', '-U', 'boutique', '-d', db, '-At', '-c', 'SELECT count(*) FROM flyway_schema_history WHERE success;').strip())
        if not before <= restored <= after or migrations < 1:
            raise ValueError('Restored order count or successful migration history does not match the backup window')
        print(json.dumps({'result': 'PASS', 'environment': environment, 'orders_before': before, 'orders_restored': restored,
                          'orders_after': after, 'successful_migrations': migrations, 'restore_seconds': round(time.monotonic() - started, 2),
                          'backup': str(backup)}, indent=2))
    finally:
        kube(cluster, *exec_args, 'dropdb', '-U', 'boutique', db)
    print('Live boutique database and PVC were preserved. Backups contain customer data; encrypt and retain them appropriately.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    doc = commands.add_parser('doctor'); doc.add_argument('--json', action='store_true')
    commands.add_parser('fetch-tools')
    commands.add_parser('fetch-manifests')
    for command in ('bootstrap', 'secrets', 'deploy', 'readiness'):
        child = commands.add_parser(command); child.add_argument('--cluster', choices=CLUSTERS, required=True)
        if command == 'secrets':
            child.add_argument('--repo-token-file'); child.add_argument('--registry-token-file', required=True); child.add_argument('--registry-user', required=True)
        if command in ('deploy', 'readiness'):
            child.add_argument('--monitoring', action='store_true')
            child.add_argument('--environment', choices=('dev', 'staging', 'production'), help='Select dev independently before staging is eligible for promotion')
    for command in ('verify', 'policy-drill', 'restore-drill', 'export-verifier'):
        child = commands.add_parser(command); child.add_argument('--environment', choices=('dev', 'staging', 'production'), required=True)
        if command == 'export-verifier': child.add_argument('--duration', default='24h')
    args = parser.parse_args()
    try:
        if args.command == 'doctor': return 0 if doctor(args.json) else 2
        if args.command == 'fetch-tools': fetch_tools()
        elif args.command == 'fetch-manifests':
            for name in LOCK['manifests']:
                pinned_manifest(name); print(f'Verified source checksum and all image digests: {name}')
        elif args.command == 'bootstrap': bootstrap(args.cluster)
        elif args.command == 'secrets': configure_secrets(args.cluster, args.repo_token_file, args.registry_token_file, args.registry_user)
        elif args.command == 'deploy': deploy(args.cluster, args.monitoring, args.environment)
        elif args.command == 'readiness': readiness(args.cluster, args.monitoring, args.environment)
        elif args.command == 'verify': verify(args.environment)
        elif args.command == 'policy-drill': policy_drill(args.environment)
        elif args.command == 'restore-drill': restore_drill(args.environment)
        elif args.command == 'export-verifier': export_verifier(args.environment, args.duration)
        return 0
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as error:
        print(f'BLOCKED: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
