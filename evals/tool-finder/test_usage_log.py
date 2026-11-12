import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / 'skills/tool-finder/scripts/usage_log.py'


class UsageLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'shared'

    def call(self, *args, success=True):
        result = subprocess.run([sys.executable, str(SCRIPT), '--log-dir', str(self.root), *args], text=True, capture_output=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0)
        return result

    def record(self, event='e1', task='t1', status='used', outcome='success'):
        args = ['record', '--event-id', event, '--task-id', task, '--category', 'docs', '--tool-url', 'https://example.org/skill', '--kind', 'skill', '--stage', 'review', '--status', status]
        if status == 'used':
            args += ['--outcome', outcome]
        return self.call(*args)

    def test_empty_report_does_not_create_directory(self):
        self.assertEqual(self.call('summary')['tools'], [])
        self.assertFalse(self.root.exists())

    def test_log_persists_across_processes_and_counts_unique_tasks(self):
        self.record()
        self.record(event='e2')
        self.record(event='e3', task='t2', outcome='failed')
        tool = self.call('summary')['tools'][0]
        self.assertEqual(tool['used_tasks'], 2)
        self.assertEqual(tool['outcomes'], {'failed': 1, 'success': 1})
        self.assertEqual(tool['categories'], {'docs': 2})

    def test_recommendation_not_counted_as_usage(self):
        self.record(status='recommended')
        self.assertEqual(self.call('summary')['tools'][0]['used_tasks'], 0)

    def test_retry_is_idempotent_and_conflict_rejected(self):
        self.assertEqual(self.record()['result'], 'recorded')
        self.assertEqual(self.record()['result'], 'already_recorded')
        result = self.call('record', '--event-id', 'e1', '--task-id', 'other', '--category', 'docs', '--tool-url', 'https://example.org/skill', '--kind', 'skill', '--stage', 'review', '--status', 'rejected', success=False)
        self.assertIn('event-id', result.stderr)
        self.assertEqual(len(list(self.root.glob('*.json'))), 1)

    def test_missing_outcome_rejected_without_writing(self):
        self.call('record', '--event-id', 'e', '--task-id', 't', '--category', 'docs', '--tool-url', 'https://example.org', '--kind', 'agent', '--stage', 'review', '--status', 'used', success=False)
        self.assertFalse(self.root.exists())

    def test_existing_file_is_not_an_empty_journal(self):
        self.root.write_text("not a directory")
        result = self.call("summary", success=False)
        self.assertIn("directory", result.stderr)

    def test_corrupt_log_is_not_silently_ignored(self):
        self.root.mkdir()
        (self.root / 'bad.json').write_text('{broken')
        self.call('summary', success=False)

    def test_invalid_url_rejected(self):
        self.call('record', '--event-id', 'e', '--task-id', 't', '--category', 'docs', '--tool-url', 'file:///secret', '--kind', 'prompt', '--stage', 'review', '--status', 'recommended', success=False)
        self.assertFalse(self.root.exists())

    def test_custom_shared_location_from_environment(self):
        env = dict(os.environ, TOOL_FINDER_LOG_DIR=str(self.root))
        result = subprocess.run([sys.executable, str(SCRIPT), 'summary'], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['log_dir'], str(self.root))


if __name__ == '__main__':
    unittest.main()
