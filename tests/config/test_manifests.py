"""Validate the retained cloud/laptop profiles and active ApplicationSet."""
import shutil, subprocess, unittest
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parents[3]/'boutique-gitops'
ORIGINS={'dev':'https://dev-boutique.narx.space','staging':'https://staging-boutique.narx.space','production':'https://shop.narx.space'}
@unittest.skipUnless(shutil.which('helm'), 'helm required')
class ManifestTests(unittest.TestCase):
 def render(self, env, profile):
  return [d for d in yaml.safe_load_all(subprocess.check_output(['python3',str(ROOT/'scripts/render.py'),env,'--profile',profile],text=True)) if d]
 def test_laptop_profiles_preserve_dependencies_and_release_ownership(self):
  for environment,origin in ORIGINS.items():
   with self.subTest(environment=environment):
    docs=self.render(environment,'laptop')
    services={d['metadata']['name']:d for d in docs if d.get('kind')=='Deployment' and d['metadata']['name'] in ('frontend','catalogue','cart','orders')}
    self.assertEqual(set(services),{'frontend','catalogue','cart','orders'})
    for name,resource in services.items():
     self.assertEqual(resource['metadata']['namespace'],'boutique-'+environment)
     self.assertRegex(resource['spec']['template']['spec']['containers'][0]['image'],r'^ghcr.io/subhankar12-spec/boutique-'+name+r'@sha256:[0-9a-f]{64}$')
    env={e['name']:e for e in services['frontend']['spec']['template']['spec']['containers'][0]['env']}
    for name,value in {'CATALOGUE_URL':'http://catalogue:8081','CART_URL':'http://cart:8082','ORDERS_URL':'http://orders:8083','PUBLIC_ORIGIN':origin,'COOKIE_SECURE':'true'}.items():self.assertEqual(env[name]['value'],value)
    self.assertEqual(env['SESSION_SECRET']['valueFrom']['secretKeyRef'],{'name':'frontend-session','key':'secret'})
    self.assertEqual({d['metadata']['name'] for d in docs if d.get('kind')=='StatefulSet'},{'postgres','redis'})
    self.assertEqual({d['metadata']['name'] for d in docs if d.get('kind')=='Rollout'},set() if environment=='dev' else set(services))
 def test_application_set_selects_retained_laptop_profiles(self):
  app=yaml.safe_load((ROOT/'applications/laptop-appset.yaml').read_text())
  self.assertEqual({e['environment'] for e in app['spec']['generators'][0]['list']['elements']},set(ORIGINS))
  source=app['spec']['template']['spec']['source']
  self.assertEqual(source['path'],'environments/{{.environment}}')
  self.assertEqual(source['helm']['valueFiles'],['values.yaml','releases.yaml','../../laptop-profiles/{{.environment}}/values.yaml'])
 def test_cloud_profiles_exclude_local_databases_and_rollouts(self):
  for env in ORIGINS:
   with self.subTest(environment=env):self.assertFalse(any(d.get('kind') in ('StatefulSet','Rollout') for d in self.render(env,'cloud')))
