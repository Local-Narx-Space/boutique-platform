#!/usr/bin/env python3
"""Explicit operator bootstrap for a local lab admin; never accepts a password."""
import argparse
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', required=True)
    parser.add_argument('--environment', choices=('dev', 'staging', 'production'), required=True)
    parser.add_argument('--email', required=True)
    parser.add_argument('--revoke', action='store_true')
    args = parser.parse_args()
    if not args.context.startswith('kind-'):
        parser.error('This helper is scoped to explicitly selected local kind clusters')
    email = args.email.strip().lower()
    if len(email) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        parser.error('Enter the email of an existing registered account')
    # SQL literal escaping is intentional; no credential is passed in command arguments.
    literal = "'" + email.replace("'", "''") + "'"
    role = 'customer' if args.revoke else 'admin'
    sql = f"BEGIN; UPDATE customers SET role='{role}' WHERE email={literal} RETURNING email,role; INSERT INTO admin_audit(actor_id,action,resource_id) SELECT id,'operator_role_change',id::text FROM customers WHERE email={literal}; COMMIT;\n"
    result = subprocess.run([
        'kubectl', '--context', args.context, '-n', 'boutique-' + args.environment,
        'exec', '-i', 'postgres-0', '--', 'sh', '-c',
        'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -U "$POSTGRES_USER" -d boutique -v ON_ERROR_STOP=1 -t -A',
    ], input=sql, text=True, capture_output=True, check=True)
    if email + '|' + role not in result.stdout:
        raise SystemExit('No account changed. Register the account in this environment first.')
    print('Updated the selected account role. Existing sessions resolve roles from the database.')


if __name__ == '__main__':
    main()
