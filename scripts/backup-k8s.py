#!/usr/bin/env python3
"""Local-lab PostgreSQL backups and isolated restores; never overwrite live data."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import time

ENVIRONMENTS = ('dev', 'staging', 'production')
LEDGER = "SELECT count(*), COALESCE(sum(total),0), md5(COALESCE(string_agg(id::text || ':' || total::text, ',' ORDER BY id),'')) FROM orders"


def execute(context, environment, arguments, *, source=None, output=None):
    command = ['kubectl', '--context', context, '-n', 'boutique-' + environment,
               'exec', '-i', 'postgres-0', '--', *arguments]
    result = subprocess.run(command, stdin=source, stdout=output or subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=240, check=False)
    if result.returncode:
        raise RuntimeError('Database operation failed; inspect the selected lab locally')
    return result.stdout.decode().strip() if output is None else None


def query(context, environment, database, sql):
    return execute(context, environment, ['psql', '-U', 'boutique', '-d', database,
                                         '-At', '-v', 'ON_ERROR_STOP=1', '-c', sql])


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def backup(context, environment, destination):
    destination = destination.resolve()
    destination.mkdir(parents=True, mode=0o700, exist_ok=True)
    if destination.stat().st_mode & 0o077:
        raise ValueError('Backup destination must be private (mode 0700)')
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = destination / f'{environment}-{stamp}-{secrets.token_hex(4)}.dump'
    before = query(context, environment, 'boutique', LEDGER)
    try:
        with path.open('xb') as output:
            execute(context, environment, ['pg_dump', '-U', 'boutique', '-d', 'boutique', '-Fc'], output=output)
            output.flush()
            os.fsync(output.fileno())
        with path.open('rb') as source:
            execute(context, environment, ['pg_restore', '--list'], source=source)
        after = query(context, environment, 'boutique', LEDGER)
        metadata = {'version': 1, 'context': context, 'environment': environment,
                    'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    'sha256': digest(path), 'bytes': path.stat().st_size,
                    'before': before, 'after': after}
        with path.with_suffix('.json').open('x') as stream:
            json.dump(metadata, stream, indent=2)
            stream.write('\n')
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    print(json.dumps({'operation': 'backup', 'environment': environment, 'file': str(path),
                      'archive_verified': True, 'bytes': metadata['bytes']}))
    return path


def restore_drill(context, environment, path):
    path = path.resolve()
    metadata = json.loads(path.with_suffix('.json').read_text())
    if (metadata.get('version') != 1 or metadata.get('context') != context
            or metadata.get('environment') != environment or metadata.get('sha256') != digest(path)):
        raise ValueError('Backup identity/checksum mismatch; refusing restore')
    # There is intentionally no option to choose a live destination database.
    database = 'boutique_restore_drill_' + secrets.token_hex(8)
    started = time.monotonic()
    execute(context, environment, ['createdb', '-U', 'boutique', database])
    try:
        with path.open('rb') as source:
            execute(context, environment, ['pg_restore', '-U', 'boutique', '-d', database,
                                           '--exit-on-error'], source=source)
        restored = query(context, environment, database, LEDGER)
        migrations = int(query(context, environment, database,
                               'SELECT count(*) FROM flyway_schema_history WHERE success'))
        if migrations < 1:
            raise ValueError('Restored database has no successful application migration')
        stable_ledger = metadata['before'] == metadata['after']
        if stable_ledger and restored != metadata['before']:
            raise ValueError('Restored order IDs/totals differ from the stable backup window')
        report = {'operation': 'restore-drill', 'environment': environment, 'success': True,
                  'orders': int(restored.split('|')[0]), 'successful_migrations': migrations,
                  'order_ledger_verified': stable_ledger,
                  'restore_seconds': round(time.monotonic() - started, 3),
                  'live_database_preserved': True}
    finally:
        execute(context, environment, ['dropdb', '-U', 'boutique', '--force', database])
    report['temporary_database_removed'] = True
    print(json.dumps(report))
    return report


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('backup', 'restore-drill'))
    parser.add_argument('--context', required=True, help='Explicit local kind context')
    parser.add_argument('--environment', required=True, choices=ENVIRONMENTS + ('all',))
    parser.add_argument('--destination', type=Path, default=Path('/var/backups/boutique'))
    parser.add_argument('--file', type=Path)
    args = parser.parse_args()
    if not args.context.startswith('kind-'):
        parser.error('This helper is for explicitly selected local kind clusters')
    if args.operation == 'restore-drill' and (not args.file or args.environment == 'all'):
        parser.error('Restore requires one environment and its exact --file')
    try:
        if args.operation == 'backup':
            for environment in ENVIRONMENTS if args.environment == 'all' else (args.environment,):
                backup(args.context, environment, args.destination)
        else:
            restore_drill(args.context, args.environment, args.file)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, KeyError):
        parser.exit(1, 'Backup/recovery failed; inspect the selected lab locally.\n')


if __name__ == '__main__':
    main()
