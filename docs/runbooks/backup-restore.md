# Backup Restore

From boutique-platform, run scripts/backup-local.sh /secure/path/orders.dump and scripts/restore-drill.sh /secure/path/orders.dump. The drill restores to boutique_restore_drill and preserves the live database. Record backup-time order count and compare after restore. Encrypt backups outside the lab, limit reads and define retention. For AWS restore RDS to a new instance, validate schema/orders, measure recovery time/data loss, then review any cutover separately.

Targets and backup/exercise cadence are in [recovery-targets.md](../recovery-targets.md). The documented schedules/off-host destinations are not implemented by these manual scripts. Follow [disaster-recovery.md](disaster-recovery.md) for replacement validation and separately reviewed cutover; record [recovery-exercise.md](recovery-exercise.md).


## Current single-owner laptop lab

Use `scripts/backup-k8s.py` for the active Kubernetes databases. The older
`backup-local.sh` and `restore-drill.sh` target Docker Compose instead.

```sh
python3 scripts/backup-k8s.py backup --context kind-mega-local --environment all
python3 scripts/backup-k8s.py restore-drill --context kind-mega-local --environment production --file /var/backups/boutique/production-TIMESTAMP-ID.dump
```

The explicit context and environment must match the backup metadata. Custom-format
pg_dump archives and SHA256 metadata live outside Git in a private directory.
Never commit or upload them as CI artifacts: they contain application data.
Restore creates a disposable database, verifies orders and Flyway history, and
removes the database even on failure. It never restores over the live database.
If no orders changed during backup, restored IDs/totals must match the recorded
fingerprint. A busy window reports no ledger verification; repeat when quiet.

The lab schedules a daily 02:30 local-time backup through
`/etc/cron.d/boutique-postgres-backup`, with a lock against overlap. Review
`/var/log/boutique-backup.log`, backup timestamps and disk space; restore again
after schema changes. Cron misses the run if the laptop is off at that time.
This local learning setup does not provide host-loss protection or automatic
backup notifications. Keep seven recent daily sets; retention is manual.
