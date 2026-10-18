"""Recovery must fail closed and must never restore into the live database."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('backup', Path(__file__).resolve().parents[2] / 'scripts/backup-k8s.py')
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


class RestoreSafety(unittest.TestCase):
    def fixture(self, directory):
        path = Path(directory) / 'production.dump'
        path.write_bytes(b'fixture archive')
        metadata = {'version': 1, 'context': 'kind-lab', 'environment': 'production',
                    'sha256': backup.digest(path), 'before': '2|300|fingerprint',
                    'after': '2|300|fingerprint'}
        path.with_suffix('.json').write_text(json.dumps(metadata))
        return path

    def test_modified_archive_and_wrong_environment_never_touch_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory)
            with patch.object(backup, 'execute') as execute:
                with self.assertRaises(ValueError): backup.restore_drill('kind-lab', 'staging', path)
                path.write_bytes(b'corrupted')
                with self.assertRaises(ValueError): backup.restore_drill('kind-lab', 'production', path)
                execute.assert_not_called()

    def test_restore_failure_cleans_only_generated_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory)
            with patch.object(backup, 'execute', side_effect=['', RuntimeError('fixture failure'), '']) as execute:
                with self.assertRaises(RuntimeError): backup.restore_drill('kind-lab', 'production', path)
                calls = [call.args[2] for call in execute.call_args_list]
                database = calls[0][-1]
                self.assertTrue(database.startswith('boutique_restore_drill_'))
                self.assertEqual(calls[1][calls[1].index('-d') + 1], database)
                self.assertEqual(calls[-1], ['dropdb', '-U', 'boutique', '--force', database])

    def test_ledger_mismatch_blocks_success_and_cleans_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory)
            with patch.object(backup, 'execute') as execute, patch.object(backup, 'query', side_effect=['1|100|different', '1']):
                with self.assertRaises(ValueError): backup.restore_drill('kind-lab', 'production', path)
                self.assertEqual(execute.call_args.args[2][0], 'dropdb')

    def test_success_reports_ledger_migrations_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory)
            with patch.object(backup, 'execute'), patch.object(backup, 'query', side_effect=['2|300|fingerprint', '1']), contextlib.redirect_stdout(io.StringIO()):
                report = backup.restore_drill('kind-lab', 'production', path)
            self.assertTrue(report['order_ledger_verified'])
            self.assertTrue(report['temporary_database_removed'])
