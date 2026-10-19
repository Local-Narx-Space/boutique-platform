"""Deployment wait gates against real Git history and native status fixtures."""
import copy
import yaml
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

PLATFORM = Path(__file__).resolve().parents[2]
SCRIPT = PLATFORM / 'scripts/wait-for-deployment.py'
SOURCE = Path(os.environ.get('BOUTIQUE_GITOPS_ROOT', PLATFORM.parent / 'boutique-gitops'))
spec = importlib.util.spec_from_file_location('deployment_wait', SCRIPT)
waiter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(waiter)
IMAGE = 'ghcr.io/subhankar12-spec/boutique-cart@sha256:' + 'a'*64
OTHER = 'ghcr.io/subhankar12-spec/boutique-cart@sha256:' + 'b'*64


class Clock:
    def __init__(self): self.now = 0
    def __call__(self): return self.now
    def sleep(self, seconds): self.now += seconds


class DeploymentWaitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='boutique-wait-test-')
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.root = self.folder/'gitops'
        shutil.copytree(SOURCE/'scripts',self.root/'scripts',ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(SOURCE/'environments',self.root/'environments')
        shutil.copytree(SOURCE/'laptop-profiles',self.root/'laptop-profiles')
        profile=self.root/'laptop-profiles/dev/values.yaml'
        data=yaml.safe_load(profile.read_text());data['progressiveDelivery']={'enabled':False}
        profile.write_text(yaml.safe_dump(data))
        values=self.root/'environments/dev/releases.yaml';data=yaml.safe_load(values.read_text())
        for service in waiter.SERVICES:data[service]['image']={'repository':'ghcr.io/subhankar12-spec/boutique-'+service,'digest':'sha256:'+'a'*64,'tag':''}
        values.write_text(yaml.safe_dump(data,sort_keys=False))
        self.git('init','-b','main')
        self.git('config','user.name','Deployment verifier test')
        self.git('config','user.email','verifier-test@example.com')
        self.git('remote','add','origin',waiter.REPOSITORY)
        self.requested = self.commit()
        self.git('update-ref','refs/remotes/origin/main',self.requested)

    def git(self,*args):
        result = subprocess.run(['git','-C',str(self.root),*args],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        return result.stdout.strip()

    def commit(self):
        self.git('add','.')
        self.git('commit','-m','Reviewed environment selection')
        return self.git('rev-parse','HEAD')

    def descendant(self,changed_image=False,protected=True):
        if changed_image:
            file = self.root/'environments/dev/releases.yaml'
            file.write_text(file.read_text().replace('a'*64,'b'*64))
        else:
            (self.root/'README.md').write_text('Unrelated reviewed change '+str(len(self.git('log','--oneline').splitlines())))
        revision=self.commit()
        if protected:self.git('update-ref','refs/remotes/origin/main',revision)
        return revision

    def history(self,requested=None):
        return waiter.TrustedHistory(self.root,'dev','cart',IMAGE,requested or self.requested)

    def application(self,revision=None,path='environments/dev'):
        source={'repoURL':waiter.REPOSITORY,'targetRevision':'main','path':path,'helm':{'releaseName':'boutique-dev','valueFiles':['values.yaml','releases.yaml']}}
        if path=='laptop-profiles/dev':
            source['path']='environments/dev';source['helm']['valueFiles'].append('../../laptop-profiles/dev/values.yaml')
        return {'apiVersion':'argoproj.io/v1alpha1','kind':'Application',
                'metadata':{'name':'boutique-dev','namespace':'argocd'},
                'spec':{'source':source,'destination':{'server':'https://kubernetes.default.svc','namespace':'boutique-dev'}},
                'status':{'sync':{'revision':revision or self.requested,'status':'Synced','comparedTo':{'source':copy.deepcopy(source)}},
                          'health':{'status':'Healthy'},'operationState':{'phase':'Succeeded'}}}

    def test_descendant_with_same_image_and_changed_chart_rejected(self):
        history=self.history()
        target=next((self.root/'environments/dev/charts').glob('boutique-cart-*.tgz'))
        data=bytearray(target.read_bytes());data[4]=(data[4]+1)%256;target.write_bytes(data)
        revision=self.commit();self.git('update-ref','refs/remotes/origin/main',revision)
        history=self.history()
        with self.assertRaisesRegex(waiter.DeploymentError,'chart'):
            history.validate_revision(revision)

    def deployments(self):
        items=[]
        for service in waiter.SERVICES:
            items.append({'apiVersion':'apps/v1','kind':'Deployment',
                'metadata':{'name':service,'namespace':'boutique-dev','generation':3},
                'spec':{'replicas':2,'template':{'spec':{'containers':[{'name':service,'image':'ghcr.io/subhankar12-spec/boutique-'+service+'@sha256:'+'a'*64}]}}},
                'status':{'replicas':2,'updatedReplicas':2,'readyReplicas':2,'availableReplicas':2,'observedGeneration':3,
                          'conditions':[{'type':'Available','status':'True'},{'type':'Progressing','status':'True','reason':'NewReplicaSetAvailable'}]}})
        return {'apiVersion':'v1','kind':'List','items':items}

    def poll(self,app=None,deployments=None,timeout=10):
        clock=Clock()
        app=app or self.application(); deployments=deployments or self.deployments()
        calls=[]
        def read(namespace,resource,**kwargs):
            calls.append((namespace,resource))
            return copy.deepcopy(app if namespace=='argocd' else deployments)
        result=waiter.wait_for_deployment(self.history(),timeout,read=read,clock=clock,sleep=clock.sleep)
        return result,calls

    def test_exact_commit_requires_native_healthy_application_and_four_rollouts(self):
        result,calls=self.poll()
        self.assertEqual(result,self.requested)
        self.assertEqual(calls,[('argocd',['application','boutique-dev']),('boutique-dev',['deployments']),('argocd',['application','boutique-dev'])])

    def test_unrelated_reviewed_descendant_returns_actual_revision(self):
        actual=self.descendant()
        result,_=self.poll(app=self.application(actual,path='laptop-profiles/dev'))
        self.assertEqual(result,actual)
        self.assertNotEqual(result,self.requested)

    def test_changed_selected_image_in_descendant_is_rejected(self):
        actual=self.descendant(changed_image=True)
        with self.assertRaisesRegex(waiter.DeploymentError,'Selected service image differs'):
            self.poll(app=self.application(actual))

    def test_unprotected_descendant_is_rejected(self):
        actual=self.descendant(protected=False)
        # This synthetic history has no network upstream; a refresh cannot
        # promote its unreviewed commit into the protected tracking branch.
        with patch.object(waiter.TrustedHistory, 'refresh_protected_main'), self.assertRaisesRegex(waiter.DeploymentError,'protected main history'):
            self.poll(app=self.application(actual))

    def test_divergent_revision_cannot_use_same_image_to_bypass_history(self):
        self.descendant()
        self.git('checkout','-b','unreviewed',self.requested)
        (self.root/'divergent.txt').write_text('Different history')
        divergent=self.commit()
        with patch.object(waiter.TrustedHistory, 'refresh_protected_main'), self.assertRaisesRegex(waiter.DeploymentError,'protected main history'):
            self.poll(app=self.application(divergent))

    def advanced_upstream(self, changed_image=False):
        upstream = self.folder / 'upstream.git'
        writer = self.folder / 'writer'
        def run(*args):
            return subprocess.run(['git', *map(str, args)], check=True, capture_output=True, text=True).stdout.strip()
        run('clone', '--bare', self.root, upstream)
        run('clone', upstream, writer)
        run('-C', writer, 'config', 'user.name', 'Reviewed upstream change')
        run('-C', writer, 'config', 'user.email', 'upstream@example.com')
        if changed_image:
            p = writer / 'environments/dev/releases.yaml'
            p.write_text(p.read_text().replace('a' * 64, 'b' * 64))
        else:
            (writer / 'README.md').write_text('Another service merged while verification waited.')
        run('-C', writer, 'add', '.')
        run('-C', writer, 'commit', '-m', 'Reviewed later merge')
        run('-C', writer, 'push', 'origin', 'main')
        return upstream, run('-C', writer, 'rev-parse', 'HEAD')

    def history_with_local_upstream(self, upstream):
        history = self.history()
        original = history.git
        def git(*args):
            if args and args[0] == 'fetch':
                # Route only the transport to a real isolated upstream. The
                # production origin URL and branch restrictions stay checked.
                self.assertEqual(args, ('fetch', '--no-tags', 'origin', 'refs/heads/main:refs/remotes/origin/main'))
                return self.git('fetch', '--no-tags', str(upstream), args[-1])
            return original(*args)
        history.git = git
        return history

    def test_later_argo_commit_is_fetched_from_protected_main(self):
        upstream, revision = self.advanced_upstream()
        history = self.history_with_local_upstream(upstream)
        missing = subprocess.run(['git', '-C', str(self.root), 'cat-file', '-e', revision], capture_output=True)
        self.assertNotEqual(missing.returncode, 0)
        self.assertEqual(history.validate_revision(revision), revision)
        self.assertEqual(history.protected_tip, revision)

    def test_refresh_does_not_allow_a_changed_selected_release(self):
        upstream, revision = self.advanced_upstream(changed_image=True)
        history = self.history_with_local_upstream(upstream)
        with self.assertRaisesRegex(waiter.DeploymentError, 'Selected service image differs'):
            history.validate_revision(revision)

    def test_refresh_failure_is_not_treated_as_verified(self):
        history = self.history()
        with patch.object(history, 'refresh_protected_main', side_effect=waiter.DeploymentError('Fetch failed')):
            with self.assertRaisesRegex(waiter.DeploymentError, 'Fetch failed'):
                history.validate_revision('c' * 40)

    def test_older_healthy_revision_does_not_satisfy_requested_descendant(self):
        requested=self.descendant()
        self.assertIsNone(waiter.application_revision(self.application(), 'dev', self.history(requested)))
        clock=Clock()
        def read(namespace,resource,**kwargs):return self.application()
        with self.assertRaisesRegex(waiter.DeploymentError,'Timed out'):
            waiter.wait_for_deployment(self.history(requested),3,read=read,clock=clock,sleep=clock.sleep)

    def test_previous_healthy_revision_waits_then_accepts_requested_revision(self):
        requested=self.descendant()
        apps=iter([self.application(),self.application(requested),self.application(requested)])
        clock=Clock()
        def read(namespace,resource,**kwargs):return next(apps) if namespace=='argocd' else self.deployments()
        result=waiter.wait_for_deployment(self.history(requested),10,read=read,clock=clock,sleep=clock.sleep)
        self.assertEqual(result,requested)
        self.assertEqual(clock.now,5)

    def test_missing_bootstrap_application_waits_for_operator_connection(self):
        apps=iter([None,self.application(),self.application()]);clock=Clock()
        def read(namespace,resource,**kwargs):return next(apps) if namespace=='argocd' else self.deployments()
        self.assertEqual(waiter.wait_for_deployment(self.history(),10,read=read,clock=clock,sleep=clock.sleep),self.requested)
        self.assertEqual(clock.now,5)
        with patch.object(waiter,'command',return_value='') as native:
            self.assertIsNone(waiter.native_json('argocd',['application','boutique-dev'],timeout=1))
            self.assertIn('--ignore-not-found',native.call_args.args[0])

    def test_shallow_and_wrong_remote_history_fail_before_kubernetes_reads(self):
        (self.root/'.git/shallow').write_text(self.requested+'\n')
        with self.assertRaisesRegex(waiter.DeploymentError,'Full Git history'):
            self.history()
        (self.root/'.git/shallow').unlink()
        self.git('remote','set-url','origin','https://github.com/other/untrusted.git')
        with self.assertRaisesRegex(waiter.DeploymentError,'approved GitOps repository'):
            self.history()

    def test_unhealthy_argo_times_out_without_deployment_success(self):
        app=self.application();app['status']['health']['status']='Progressing'
        with self.assertRaisesRegex(waiter.DeploymentError,'Timed out'):
            self.poll(app=app,timeout=3)

    def test_sync_errors_fail_even_if_previous_health_remains_healthy(self):
        for changed in ('comparison','operation'):
            app=self.application()
            if changed=='comparison':app['status']['conditions']=[{'type':'ComparisonError','message':'sensitive error intentionally not echoed'}]
            else:app['status']['operationState']['phase']='Failed'
            with self.assertRaises(waiter.DeploymentError):self.poll(app=app)

    def test_wrong_profile_repository_destination_or_inline_override_is_rejected(self):
        invalid=[]
        for key,value in [('path','services/cart/unapproved-path'),('repoURL','https://github.com/other/repo.git'),('targetRevision','unreviewed')]:
            app=self.application();app['spec']['source'][key]=value;invalid.append(app)
        app=self.application();app['spec']['destination']['namespace']='boutique-production';invalid.append(app)
        app=self.application();app['metadata']['name']='lab-boutique-dev';invalid.append(app)
        app=self.application();app['spec']['source']['customRenderer']={'images':[IMAGE]};invalid.append(app)
        for app in invalid:
            with self.assertRaises(waiter.DeploymentError):self.poll(app=app)

    def test_incomplete_other_service_blocks_selected_service_verification(self):
        deployments=self.deployments();deployments['items'][0]['status']['readyReplicas']=1
        with self.assertRaisesRegex(waiter.DeploymentError,'Timed out'):
            self.poll(deployments=deployments,timeout=3)

    def test_stale_controller_generation_and_wrong_selected_image_cannot_pass(self):
        for changed in ('generation','image'):
            deployments=self.deployments()
            if changed=='generation':deployments['items'][2]['status']['observedGeneration']=2
            else:deployments['items'][2]['spec']['template']['spec']['containers'][0]['image']=OTHER
            with self.assertRaisesRegex(waiter.DeploymentError,'Timed out'):
                self.poll(deployments=deployments,timeout=3)

    def test_progress_deadline_error_fails_immediately(self):
        deployments=self.deployments()
        deployments['items'][3]['status']['conditions'][1]={'type':'Progressing','status':'False','reason':'ProgressDeadlineExceeded'}
        with self.assertRaisesRegex(waiter.DeploymentError,'progress deadline'):
            self.poll(deployments=deployments)

    def test_concurrent_sync_must_settle_before_returning_revision(self):
        actual=self.descendant()
        apps=iter([self.application(),self.application(actual),self.application(actual),self.application(actual)])
        clock=Clock()
        def read(namespace,resource,**kwargs):return next(apps) if namespace=='argocd' else self.deployments()
        result=waiter.wait_for_deployment(self.history(),10,read=read,clock=clock,sleep=clock.sleep)
        self.assertEqual(result,actual)
        self.assertEqual(clock.now,5)

    def test_invalid_native_json_and_read_errors_never_become_success(self):
        with patch.object(waiter,'command',return_value='not-json'):
            with self.assertRaisesRegex(waiter.DeploymentError,'invalid JSON'):
                waiter.native_json('argocd',['application','boutique-dev'],timeout=1)
        with patch.object(waiter,'command',return_value='null'):
            with self.assertRaisesRegex(waiter.DeploymentError,'not an object'):
                waiter.native_json('argocd',['application','boutique-dev'],timeout=1)
        with patch.object(waiter,'command',side_effect=waiter.DeploymentError('API read denied')):
            with self.assertRaisesRegex(waiter.DeploymentError,'denied'):
                waiter.wait_for_deployment(self.history(),1)

    def test_git_command_errors_cannot_be_treated_as_pending_or_trusted_ancestry(self):
        history=self.history()
        with patch.object(waiter.subprocess,'run',return_value=subprocess.CompletedProcess(['git'],2,stdout='',stderr='error')):
            with self.assertRaisesRegex(waiter.DeploymentError,'read failed'):
                history.validate_revision(self.requested)

    def test_cli_uses_only_scoped_get_reads_and_writes_actual_full_sha(self):
        actual=self.descendant()
        app_file=self.folder/'application.json';app_file.write_text(json.dumps(self.application(actual)))
        deployment_file=self.folder/'deployments.json';deployment_file.write_text(json.dumps(self.deployments()))
        log=self.folder/'kubectl-reads.jsonl'
        binary=self.folder/'bin';binary.mkdir()
        kubectl=binary/'kubectl'
        kubectl.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
with Path(os.environ['READ_LOG']).open('a') as f:f.write(json.dumps(args)+'\\n')
if 'get' not in args or 'secrets' in args:sys.exit(19)
namespace=args[args.index('-n')+1]
resource=args[args.index('get')+1]
if namespace=='argocd' and resource=='application':file=os.environ['ARGO_FIXTURE']
elif namespace=='boutique-dev' and resource=='deployments':file=os.environ['DEPLOYMENT_FIXTURE']
else:sys.exit(20)
print(Path(file).read_text())
''')
        kubectl.chmod(0o755)
        output=self.folder/'reports/actual-commit.txt'
        env=os.environ.copy();env.update(PATH=str(binary)+os.pathsep+env['PATH'],ARGO_FIXTURE=str(app_file),DEPLOYMENT_FIXTURE=str(deployment_file),READ_LOG=str(log))
        result=subprocess.run([sys.executable,str(SCRIPT),'--environment','dev','--commit',self.requested,'--service','cart','--image',IMAGE,'--gitops-root',str(self.root),'--timeout','20','--output',str(output)],env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),actual)
        self.assertEqual(output.read_text(),actual+'\n')
        reads=[json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(len(reads),3)
        for args in reads:
            self.assertIn('get',args);self.assertNotIn('secrets',args)
            self.assertTrue(any(arg.startswith('--request-timeout=') for arg in args))


if __name__=='__main__':unittest.main()
