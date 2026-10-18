"""Exercise monitoring scopes, persistence and lab overrides through real Helm."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[3] / 'boutique-gitops'


@unittest.skipUnless(shutil.which('helm'), 'Helm required for monitoring render checks')
class MonitoringHelmTests(unittest.TestCase):
    def render(self, profile, image_values=None, incident=True):
        args=['helm','template','boutique-monitoring-'+profile,str(ROOT/'monitoring'),'-f',str(ROOT/'monitoring/profiles'/profile/'values.yaml')]
        if incident:args += ['--set','incidentBridge.enabled=true']
        if image_values:
            args += ['--set','incidentBridge.image.tag=','--set','incidentBridge.image.digest='+image_values]
        result=subprocess.run(args,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        resources=[d for d in yaml.safe_load_all(result.stdout) if d]
        identities=[(r['kind'],r['metadata'].get('namespace',''),r['metadata']['name']) for r in resources]
        self.assertEqual(len(identities),len(set(identities)))
        return resources

    def resource(self, resources, kind, name):
        return next(d for d in resources if d['kind']==kind and d['metadata']['name']==name)

    def test_default_monitoring_is_slack_only_without_incident_workers(self):
        for profile in ('homelab','nonprod','production'):
            resources=self.render(profile, incident=False)
            self.assertFalse(any(d['metadata']['name'].startswith('incident-bridge') for d in resources))
            config=yaml.safe_load(self.resource(resources,'ConfigMap','boutique-alertmanager')['data']['alertmanager.yml'])
            self.assertEqual([r['name'] for r in config['receivers']], ['slack','slack-production','slack-nonprod'] if profile=='homelab' else ['slack'])
            deployment=self.resource(resources,'Deployment','alertmanager')
            volume=next(v for v in deployment['spec']['template']['spec']['volumes'] if v['name']=='secrets')
            self.assertEqual([i['key'] for i in volume['secret']['items']],['slack_webhook','slack_webhook'] if profile=='homelab' else ['slack_webhook'])

    def test_profile_target_and_rbac_scopes_match(self):
        expected={'homelab':['boutique-dev','boutique-staging','boutique-production'],'nonprod':['boutique-dev','boutique-staging'],'production':['boutique-production']}
        for profile,namespaces in expected.items():
            with self.subTest(profile=profile):
                resources=self.render(profile)
                config=yaml.safe_load(self.resource(resources,'ConfigMap','boutique-prometheus')['data']['prometheus.yml'])
                for job in config['scrape_configs']:
                    if job['job_name'] in ('boutique','orders'):
                        self.assertEqual(job['kubernetes_sd_configs'][0]['namespaces']['names'],namespaces)
                roles=[d for d in resources if d['kind']=='Role' and d['metadata']['name']=='monitoring-pod-discovery']
                self.assertEqual({r['metadata']['namespace'] for r in roles},set(namespaces) | ({'monitoring','monitoring-logs'} if profile=='homelab' else set()))
                for role in roles:
                    for rule in role['rules']:
                        self.assertEqual(rule['resources'],['pods'])
                        self.assertEqual(set(rule['verbs']),{'get','list','watch'})
                alloy=self.resource(resources,'DaemonSet','alloy')['spec']['template']['spec']['containers'][0]
                labels={e['name']:e.get('value') for e in alloy['env']}
                self.assertEqual(labels['CLUSTER_NAME'],profile)

    def test_shared_monitoring_collectors_are_scoped_and_incident_alerts_are_absent(self):
        resources=self.render('homelab', incident=False)
        config=yaml.safe_load(self.resource(resources,'ConfigMap','boutique-prometheus')['data']['prometheus.yml'])
        self.assertFalse(any(j['job_name']=='incident-bridge' for j in config['scrape_configs']))
        rules=yaml.safe_load(self.resource(resources,'ConfigMap','boutique-prometheus')['data']['rules.yml'])
        alerts=[rule for group in rules['groups'] for rule in group['rules'] if 'alert' in rule]
        self.assertFalse(any(rule['alert'].startswith('Incident') for rule in alerts))
        missing=[rule for rule in alerts if rule['alert']=='BoutiqueServiceMissing']
        self.assertEqual({(rule['labels']['environment'],rule['labels']['service']) for rule in missing},
                         {(e,s) for e in ('dev','staging','production') for s in ('frontend','catalogue','cart','orders')})
        self.assertFalse(any(r['kind'] in ('ClusterRole','ClusterRoleBinding') for r in resources))
        # Host collectors must not land in the Restricted core namespace.
        for workload in resources:
            if workload['kind'] in ('Deployment','DaemonSet') and workload['metadata']['namespace']=='monitoring':
                self.assertFalse(any('hostPath' in volume for volume in workload['spec']['template']['spec'].get('volumes',[])))
        exporter=self.resource(resources,'DaemonSet','node-exporter')
        self.assertEqual(exporter['metadata']['namespace'],'monitoring-logs')
        self.assertTrue(exporter['spec']['template']['spec']['securityContext']['runAsNonRoot'])
        self.assertFalse(exporter['spec']['template']['spec'].get('hostNetwork',False))

        roles=[r for r in resources if r['kind']=='Role' and r['metadata']['name']=='kube-state-metrics']
        self.assertEqual(len(roles),5)
        self.assertFalse(any('secrets' in rule['resources'] for role in roles for rule in role['rules']))
        grafana=self.resource(resources,'ConfigMap','boutique-grafana')['data']
        self.assertEqual({'boutique.json','platform.json','slo.json'} & set(grafana),{'boutique.json','platform.json','slo.json'})
        self.assertFalse(any('incident' in p['title'].lower() for p in json.loads(grafana['boutique.json'])['panels']))

    def test_production_queue_uses_two_workers_and_scoped_secret(self):
        resources=self.render('production')
        deployment=self.resource(resources,'Deployment','incident-bridge')
        self.assertEqual(deployment['spec']['replicas'],2)
        pod=deployment['spec']['template']['spec'];container=pod['containers'][0]
        env={e['name']:e for e in container['env']}
        self.assertNotIn('QUEUE_PATH',env)
        self.assertEqual(env['QUEUE_DATABASE_URL']['valueFrom']['secretKeyRef']['name'],'monitoring-incident-queue')
        self.assertEqual(env['SERVICENOW_MODE']['value'],'scripted')
        self.assertFalse(any(d['kind']=='PersistentVolumeClaim' and d['metadata']['name']=='incident-bridge-data' for d in resources))
        self.assertEqual(self.resource(resources,'PodDisruptionBudget','incident-bridge')['spec']['minAvailable'],1)

    def test_active_homelab_has_no_mock_receivers_or_queue(self):
        resources=self.render('homelab', incident=False)
        self.assertFalse(any(d['metadata']['name'] in ('mock-receivers','incident-queue-postgres') for d in resources))
        self.assertFalse((ROOT/'lab-profiles').exists())

    def test_one_reviewed_image_selection_reaches_every_retained_profile(self):
        digest='sha256:'+'a'*64
        for profile in ('homelab','nonprod','production'):
            with self.subTest(profile=profile):
                resources=self.render(profile, image_values=digest)
                image=self.resource(resources,'Deployment','incident-bridge')['spec']['template']['spec']['containers'][0]['image']
                self.assertEqual(image,'ghcr.io/subhankar12-spec/boutique-incident-bridge@'+digest)

    def test_mutable_image_and_invalid_profile_are_rejected(self):
        for setting in ('incidentBridge.image.tag=latest','incidentBridge.image.digest=sha256:short','profile=unknown'):
            result=subprocess.run(['helm','template','monitoring',str(ROOT/'monitoring'),'--set',setting],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)

    def test_config_template_strings_and_dashboard_survive(self):
        resources=self.render('production')
        config=self.resource(resources,'ConfigMap','boutique-prometheus')['data']
        self.assertIn('{{ $labels.service }}',config['rules.yml'])
        dashboards=[d for d in resources if d['kind']=='ConfigMap' and any(k.endswith('.json') for k in d.get('data',{}))]
        self.assertTrue(dashboards)
        for d in dashboards:
            for key,value in d['data'].items():
                if key.endswith('.json'):self.assertEqual(len(json.loads(value)['panels']),13)

    def test_active_project_admits_every_rendered_resource_with_scoped_namespaces(self):
        for profile in ('homelab',):
            docs=list(yaml.safe_load_all((ROOT/'applications'/('monitoring-'+profile+'.yaml')).read_text()))
            project=next(d for d in docs if d['kind']=='AppProject')['spec']
            namespaces={d['namespace'] for d in project['destinations']}
            for resource in self.render(profile, incident=False):
                group=resource['apiVersion'].split('/')[0] if '/' in resource['apiVersion'] else ''
                kind=resource['kind'];namespace=resource['metadata'].get('namespace')
                allowed=project['namespaceResourceWhitelist'] if namespace else project['clusterResourceWhitelist']
                self.assertIn({'group':group,'kind':kind},allowed)
                if namespace:self.assertIn(namespace,namespaces)

    def test_active_argo_source_selects_chart_and_profile_values(self):
        for profile in ('homelab',):
            docs=list(yaml.safe_load_all((ROOT/'applications'/('monitoring-'+profile+'.yaml')).read_text()))
            source=next(d for d in docs if d['kind']=='Application')['spec']['source']
            self.assertEqual(source['path'],'monitoring')
            self.assertEqual(source['helm'],{'releaseName':'boutique-monitoring-'+profile,'valueFiles':['profiles/'+profile+'/values.yaml']})


if __name__ == '__main__':
    unittest.main()
