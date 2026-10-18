#!/usr/bin/env python3
"""Build/run ONLY the disposable localhost commerce fixture. Never touches Kubernetes."""
import argparse
import io
import json
from pathlib import Path
import subprocess
import tarfile
import time
import urllib.request

PREFIX = 'boutique-customer-'
NETWORK = 'boutique-customer-test'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('up', 'down'))
    parser.add_argument('--workspace', type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument('--engine-container', help='Optional existing container containing a private Docker CLI/daemon connection')
    parser.add_argument('--skip-build', action='store_true')
    args = parser.parse_args()
    engine = ['docker']
    if args.engine_container:
        engine += ['exec', '-i', '-e', 'DOCKER_CONFIG=/tmp/boutique-customer-docker', args.engine_container, 'docker']
    names = ('frontend', 'catalogue', 'cart', 'orders', 'payment-fixture', 'redis', 'pg')

    def command(arguments, data=None, check=True, quiet=False):
        result = subprocess.run(engine + arguments, input=data, stdout=subprocess.PIPE if quiet else None,
                                stderr=subprocess.PIPE if quiet else None, check=check)
        return result

    def exists(kind, name):
        return command([kind, 'inspect', name], check=False, quiet=True).returncode == 0

    if args.action == 'down':
        for name in names:
            if exists('container', PREFIX + name):
                command(['rm', '-f', PREFIX + name], quiet=True)
        if exists('network', NETWORK): command(['network', 'rm', NETWORK], quiet=True)
        print('Removed only disposable customer fixture containers/network. Fixture data was deleted.')
        return

    def build(root, tag, dockerfile='Dockerfile'):
        files = subprocess.check_output(['git', '-C', str(root), 'ls-files', '--cached', '--others', '--exclude-standard', '-z']).split(b'\0')
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w', format=tarfile.USTAR_FORMAT) as package:
            for name in files:
                if not name: continue
                relative = name.decode()
                source = root / relative
                if source.is_file() and not source.is_symlink():
                    info = package.gettarinfo(str(source), arcname=relative)
                    info.mtime = int(info.mtime)
                    with source.open('rb') as reader: package.addfile(info, reader)
        print('Building ' + tag, flush=True)
        # Keep lengthy build logs local; never print runtime credential environments.
        with Path('/tmp/boutique-customer-build.log').open('ab') as log:
            subprocess.run(engine + ['build'] + ([] if dockerfile == 'Dockerfile' else ['-f', dockerfile]) + ['-t', tag, '-'], input=archive.getvalue(), stdout=log, stderr=log, check=True)

    if not args.skip_build:
        for service in ('orders', 'catalogue', 'cart', 'frontend'):
            build(args.workspace / ('boutique-' + service), 'boutique-' + service + ':customer-local')
        build(args.workspace / 'boutique-platform', 'boutique-payment-fixture:local', 'tests/customer/Dockerfile.fixture')
    if not exists('network', NETWORK): command(['network', 'create', NETWORK], quiet=True)

    def run(name, image, env=None, ports=None, alias=None, memory='128m', cpus='0.5', extra=None):
        full = PREFIX + name
        if exists('container', full):
            command(['start', full], quiet=True)
            return
        arguments = ['run', '-d', '--name', full, '--network', NETWORK, '--network-alias', alias or name,
                     '--memory', memory, '--cpus', cpus, '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true']
        for key, value in (env or {}).items(): arguments += ['-e', key + '=' + value]
        for binding in ports or []: arguments += ['-p', binding]
        command(arguments + (extra or []) + [image], quiet=True)

    # The database has only synthetic disposable data and is published on no host port.
    # PostgreSQL init needs its standard privilege setup, unlike the non-root app containers.
    if not exists('container', PREFIX + 'pg'):
        command(['run', '-d', '--name', PREFIX + 'pg', '--network', NETWORK, '--network-alias', 'postgres',
                 '--memory', '160m', '--cpus', '0.5', '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', '-e', 'POSTGRES_DB=boutique',
                 'postgres:16-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea'], quiet=True)
    else: command(['start', PREFIX + 'pg'], quiet=True)
    run('redis', 'redis:7.4-alpine@sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499', memory='64m', extra=['--user', '999:1000', '--tmpfs', '/data:uid=999,gid=1000'])
    run('payment-fixture', 'boutique-payment-fixture:local', alias='razorpay-fixture', ports=['127.0.0.1:19090:19090'], memory='64m')
    run('orders', 'boutique-orders:customer-local', env={
        'DATABASE_URL': 'jdbc:postgresql://postgres:5432/boutique', 'DATABASE_USER': 'postgres', 'DATABASE_PASSWORD': '',
        'CATALOGUE_URL': 'http://catalogue:8081', 'CART_URL': 'http://cart:8082', 'COMMERCE_ENABLED': 'true',
        'RAZORPAY_KEY_ID': 'rzp_test_fixture', 'RAZORPAY_KEY_SECRET': 'fixture-api-secret',
        'RAZORPAY_WEBHOOK_SECRET': 'fixture-webhook-secret', 'RAZORPAY_API_BASE': 'http://razorpay-fixture:19090/v1',
        'ALLOW_PAYMENT_TEST_FIXTURE': 'true', 'JAVA_TOOL_OPTIONS': '-Xmx256m -XX:ActiveProcessorCount=2',
    }, memory='512m', cpus='1')
    run('catalogue', 'boutique-catalogue:customer-local', env={'COMMERCE_URL': 'http://orders:8083'}, memory='64m')
    run('cart', 'boutique-cart:customer-local', env={'REDIS_URL': 'redis://redis:6379/0'})
    run('frontend', 'boutique-frontend:customer-local', env={
        'SESSION_SECRET': 'disposable-fixture-only-session-secret-never-use-in-a-real-lab',
        'PUBLIC_ORIGIN': 'http://127.0.0.1:18088', 'COOKIE_SECURE': 'false', 'CATALOGUE_URL': 'http://catalogue:8081',
        'CART_URL': 'http://cart:8082', 'ORDERS_URL': 'http://orders:8083',
    }, ports=['127.0.0.1:18088:8080'])
    for _ in range(120):
        try:
            with urllib.request.urlopen('http://127.0.0.1:18088/health/ready', timeout=8) as response:
                if response.status == 200:
                    print('Ready: http://127.0.0.1:18088 — LOCAL SIMULATOR, not a Razorpay sandbox transaction.')
                    return
        except Exception: pass
        time.sleep(1)
    raise SystemExit('Fixture did not become ready. Inspect only boutique-customer-* container logs.')


if __name__ == '__main__':
    main()
