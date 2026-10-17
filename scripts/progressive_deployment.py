"""Read-only verification of the version actually serving a blue/green frontend."""
import re


class ProgressiveError(ValueError):
    pass


def observed(value):
    # Rollouts v1.10 represents observed generations as decimal strings.
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r'[0-9]+', value):
        return int(value)
    raise ProgressiveError('Invalid Rollout observed generation')


def ready(deployment, rollout, replicasets, service, environment, image):
    namespace = 'boutique-' + environment
    for item, kind in [(deployment, 'Deployment'), (rollout, 'Rollout'), (service, 'Service')]:
        meta = item.get('metadata', {})
        if item.get('kind') != kind or meta.get('namespace') != namespace or meta.get('name') != 'frontend' or meta.get('deletionTimestamp'):
            raise ProgressiveError('Progressive resource identity differs from the expected frontend')
    spec, status = rollout.get('spec', {}), rollout.get('status', {})
    if spec.get('workloadRef') != {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'name': 'frontend', 'scaleDown': 'onsuccess'}:
        raise ProgressiveError('Unexpected progressive workload reference')
    strategy = spec.get('strategy', {}).get('blueGreen', {})
    if not spec.get('progressDeadlineAbort') or strategy.get('activeService') != 'frontend' or strategy.get('previewService') != 'frontend-preview':
        raise ProgressiveError('Expected abort-enabled blue/green strategy')
    for phase, template in [('prePromotionAnalysis', 'frontend-preview-check'), ('postPromotionAnalysis', 'frontend-serving-check')]:
        if strategy.get(phase, {}).get('templates') != [{'templateName': template}]:
            raise ProgressiveError('Required progressive analysis is missing')
    conditions = {item.get('type'): item for item in status.get('conditions', [])}
    if status.get('abort') is True or status.get('phase') in ('Degraded', 'Error') or conditions.get('Progressing', {}).get('reason') == 'ProgressDeadlineExceeded':
        raise ProgressiveError('Release aborted or failed; stable traffic recovery is not release success')
    if status.get('phase') != 'Healthy' or status.get('pauseConditions') or status.get('controllerPause'):
        return False
    generation = rollout['metadata'].get('generation')
    deployment_generation = deployment['metadata'].get('generation')
    if type(generation) is not int or type(deployment_generation) is not int:
        raise ProgressiveError('Missing workload generations')
    if observed(status.get('observedGeneration', 0)) < generation or observed(status.get('workloadObservedGeneration', 0)) < deployment_generation:
        return False
    if any(conditions.get(name, {}).get('status') != 'True' for name in ('Healthy', 'Completed', 'Available')):
        return False
    desired = spec.get('replicas')
    if type(desired) is not int or desired < 1:
        raise ProgressiveError('Invalid progressive replica count')
    stable = status.get('stableRS', '')
    if not isinstance(stable, str) or not re.fullmatch(r'[a-z0-9]{6,20}', stable):
        return False
    if status.get('promoteFull') is True:
        raise ProgressiveError('Manual full promotion bypassed the required analysis gate')
    revision = rollout['metadata'].get('annotations', {}).get('rollout.argoproj.io/revision', '')
    if not isinstance(revision, str) or not re.fullmatch(r'[1-9][0-9]*', revision):
        raise ProgressiveError('Missing controller revision')
    # Initial migration has no previous Rollout release and the controller skips
    # analysis. It must use the already-verified baseline image. Later revisions
    # must have successful pre/post analysis for this exact hash and revision.
    if revision != '1':
        for phase, suffix in [('prePromotionAnalysisRunStatus', 'pre'), ('postPromotionAnalysisRunStatus', 'post')]:
            analysis = status.get('blueGreen', {}).get(phase, {})
            if analysis.get('status') != 'Successful' or analysis.get('name') != 'frontend-' + stable + '-' + revision + '-' + suffix:
                return False
    if stable != status.get('currentPodHash') or stable != status.get('blueGreen', {}).get('activeSelector'):
        return False
    selector = service.get('spec', {}).get('selector', {})
    if selector.get('rollouts-pod-template-hash') != stable or selector.get('app.kubernetes.io/name') != 'frontend':
        raise ProgressiveError('Active Service does not select the verified stable ReplicaSet')
    matches = [item for item in replicasets.get('items', []) if item.get('metadata', {}).get('name') == 'frontend-' + stable]
    if len(matches) != 1:
        return False
    active = matches[0]
    meta = active.get('metadata', {})
    owners = [item for item in meta.get('ownerReferences', []) if item.get('controller') is True]
    if active.get('kind') != 'ReplicaSet' or meta.get('namespace') != namespace or meta.get('deletionTimestamp'):
        raise ProgressiveError('Invalid serving ReplicaSet identity')
    if len(owners) != 1 or owners[0].get('kind') != 'Rollout' or owners[0].get('uid') != rollout['metadata'].get('uid'):
        raise ProgressiveError('Serving ReplicaSet belongs to another controller')
    if meta.get('labels', {}).get('rollouts-pod-template-hash') != stable:
        raise ProgressiveError('Serving ReplicaSet hash does not match the Service')
    containers = active.get('spec', {}).get('template', {}).get('spec', {}).get('containers', [])
    frontend = [container for container in containers if container.get('name') == 'frontend']
    if len(frontend) != 1 or frontend[0].get('image') != image:
        raise ProgressiveError('Serving frontend image differs from the approved release')
    if deployment.get('spec', {}).get('replicas') != 0:
        return False  # Reference Deployment is scaled down only after success.
    measured = active.get('status', {})
    if observed(measured.get('observedGeneration', 0)) < meta.get('generation', 1):
        return False
    return active.get('spec', {}).get('replicas') == desired and all(measured.get(name) == desired for name in ('replicas', 'readyReplicas', 'availableReplicas'))
