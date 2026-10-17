"""Stable traffic after an abort must never satisfy the release-success gate."""
import copy
import importlib.util
from pathlib import Path
import unittest
import yaml

source = Path(__file__).resolve().parents[2] / 'scripts/progressive_deployment.py'
spec = importlib.util.spec_from_file_location('progressive', source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
IMAGE = 'ghcr.io/subhankar12-spec/boutique-frontend@sha256:' + 'a' * 64


class ProgressiveVerification(unittest.TestCase):
    def setUp(self):
        meta = {'name': 'frontend', 'namespace': 'boutique-staging', 'generation': 3}
        self.deployment = {'kind': 'Deployment', 'metadata': copy.deepcopy(meta), 'spec': {'replicas': 0}}
        self.rollout = {'kind': 'Rollout', 'metadata': {**meta, 'uid': 'rollout-uid', 'annotations': {'rollout.argoproj.io/revision': '2'}},
                        'spec': {'replicas': 2, 'progressDeadlineAbort': True,
                                 'workloadRef': {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'name': 'frontend', 'scaleDown': 'onsuccess'},
                                 'strategy': {'blueGreen': {'activeService': 'frontend', 'previewService': 'frontend-preview',
                                                          'prePromotionAnalysis': {'templates': [{'templateName': 'frontend-preview-check'}]},
                                                          'postPromotionAnalysis': {'templates': [{'templateName': 'frontend-serving-check'}]}}}},
                        'status': {'phase': 'Healthy', 'observedGeneration': '3', 'workloadObservedGeneration': '3',
                                   'stableRS': 'abc123', 'currentPodHash': 'abc123', 'blueGreen': {'activeSelector': 'abc123',
                                       'prePromotionAnalysisRunStatus': {'name': 'frontend-abc123-2-pre', 'status': 'Successful'},
                                       'postPromotionAnalysisRunStatus': {'name': 'frontend-abc123-2-post', 'status': 'Successful'}},
                                   'conditions': [{'type': name, 'status': 'True'} for name in ['Healthy', 'Available', 'Completed']]}}
        self.service = {'kind': 'Service', 'metadata': copy.deepcopy(meta),
                        'spec': {'selector': {'app.kubernetes.io/name': 'frontend', 'rollouts-pod-template-hash': 'abc123'}}}
        self.rs = {'kind': 'ReplicaSet', 'metadata': {**meta, 'name': 'frontend-abc123',
                    'labels': {'rollouts-pod-template-hash': 'abc123'},
                    'ownerReferences': [{'kind': 'Rollout', 'uid': 'rollout-uid', 'controller': True}]},
                   'spec': {'replicas': 2, 'template': {'spec': {'containers': [{'name': 'frontend', 'image': IMAGE}]}}},
                   'status': {'observedGeneration': 3, 'replicas': 2, 'readyReplicas': 2, 'availableReplicas': 2}}

    def ready(self):
        return module.ready(self.deployment, self.rollout, {'items': [self.rs]}, self.service, 'staging', IMAGE)

    def test_verified_active_image_and_completed_analysis_pass(self):
        self.assertTrue(self.ready())

    def test_abort_rejects_even_when_stable_traffic_is_healthy(self):
        self.rollout['status']['abort'] = True
        with self.assertRaisesRegex(module.ProgressiveError, 'not release success'):
            self.ready()

    def test_degraded_rejects_without_abort_flag(self):
        self.rollout['status']['phase'] = 'Degraded'
        with self.assertRaises(module.ProgressiveError):
            self.ready()

    def test_unobserved_workload_and_rollout_wait(self):
        for field in ['observedGeneration', 'workloadObservedGeneration']:
            self.rollout['status'][field] = '2'
            self.assertFalse(self.ready())
            self.rollout['status'][field] = '3'

    def test_old_stable_hash_does_not_bless_new_candidate(self):
        self.rollout['status']['currentPodHash'] = 'xyz123'
        self.assertFalse(self.ready())

    def test_wrong_service_selector_rejects(self):
        self.service['spec']['selector']['rollouts-pod-template-hash'] = 'xyz123'
        with self.assertRaises(module.ProgressiveError):
            self.ready()

    def test_serving_image_drift_rejects(self):
        self.rs['spec']['template']['spec']['containers'][0]['image'] = IMAGE.replace('a' * 64, 'b' * 64)
        with self.assertRaises(module.ProgressiveError):
            self.ready()

    def test_wrong_replica_owner_rejects(self):
        self.rs['metadata']['ownerReferences'][0]['uid'] = 'another-controller'
        with self.assertRaises(module.ProgressiveError):
            self.ready()

    def test_missing_post_promotion_analysis_rejects(self):
        self.rollout['spec']['strategy']['blueGreen'].pop('postPromotionAnalysis')
        with self.assertRaises(module.ProgressiveError):
            self.ready()

    def test_unready_active_replica_and_native_source_wait(self):
        self.rs['status']['availableReplicas'] = 1
        self.assertFalse(self.ready())
        self.rs['status']['availableReplicas'] = 2
        self.deployment['spec']['replicas'] = 1
        self.assertFalse(self.ready())

    def test_wrong_environment_rejects(self):
        self.service['metadata']['namespace'] = 'boutique-production'
        with self.assertRaises(module.ProgressiveError):
            self.ready()

    def test_manual_full_promotion_is_rejected(self):
        self.rollout['status']['promoteFull'] = True
        with self.assertRaises(module.ProgressiveError):
            self.ready()

    def test_previous_or_unfinished_analysis_does_not_pass(self):
        summary = self.rollout['status']['blueGreen']['postPromotionAnalysisRunStatus']
        summary['status'] = 'Running'
        self.assertFalse(self.ready())
        summary.update(status='Successful', name='frontend-old123-1-post')
        self.assertFalse(self.ready())

    def polling_fixture(self):
        from test_wait_for_deployment import DeploymentWaitTests, Clock, waiter
        helper = DeploymentWaitTests('test_unrelated_reviewed_descendant_returns_actual_revision')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        path = helper.root / 'environments/dev/values.yaml'
        values = yaml.safe_load(path.read_text())
        values['progressiveDelivery'] = {'enabled': True, 'apiServerCIDR': '172.18.0.2/32'}
        path.write_text(yaml.safe_dump(values))
        helper.requested = helper.commit()
        helper.git('update-ref', 'refs/remotes/origin/main', helper.requested)
        app, deployments = helper.application(), helper.deployments()
        next(item for item in deployments['items'] if item['metadata']['name'] == 'frontend')['spec']['replicas'] = 0
        for item in [self.deployment, self.rollout, self.rs, self.service]:
            item['metadata']['namespace'] = 'boutique-dev'
        calls = []

        def read(namespace, resource, **kwargs):
            calls.append((namespace, resource))
            if namespace == 'argocd':
                return copy.deepcopy(app)
            value = {'deployments': deployments, 'rollout.argoproj.io': self.rollout,
                     'replicasets': {'items': [self.rs]}, 'service': self.service}[resource[0]]
            return copy.deepcopy(value)
        return helper, app, calls, read, Clock(), waiter

    def test_poll_measures_rollout_service_and_active_replica_set(self):
        helper, app, calls, read, clock, waiter = self.polling_fixture()
        history = helper.history()
        self.assertEqual(waiter.wait_for_deployment(history, 10, read=read, clock=clock, sleep=clock.sleep), helper.requested)
        self.assertTrue(history.progressive_frontend)
        self.assertIn(('boutique-dev', ['replicasets']), calls)
        self.assertIn(('boutique-dev', ['service', 'frontend']), calls)

    def test_degraded_abort_fails_without_waiting_for_timeout(self):
        helper, app, calls, read, clock, waiter = self.polling_fixture()
        app['status']['health']['status'] = 'Degraded'
        self.rollout['status'].update(phase='Degraded', abort=True)
        with self.assertRaisesRegex(waiter.DeploymentError, 'promotion is blocked'):
            waiter.wait_for_deployment(helper.history(), 10, read=read, clock=clock, sleep=clock.sleep)
        self.assertEqual(clock.now, 0)


if __name__ == '__main__':
    unittest.main()
