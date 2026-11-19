import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'repo_cache.py'
REPO = 'https://github.com/Example/Tools.git/'
FACTS = {'commit': 'a' * 40, 'components': [{'kind': 'skill', 'path': 'skills/demo/SKILL.md'}]}


class RepoCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'cache'

    def run_cache(self, command, repo=REPO, data=None, flags=()):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), '--cache-dir', str(self.root), command, repo, *flags],
            input=json.dumps(data) if data is not None else None, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_persists_and_normalizes_in_separate_processes(self):
        self.assertEqual(self.run_cache('get')['status'], 'miss')
        self.assertEqual(self.run_cache('put', data=FACTS)['status'], 'stored')
        hit = self.run_cache('get', 'https://github.com/example/tools')
        self.assertEqual(hit['status'], 'hit')
        self.assertEqual(hit['facts'], FACTS)
        self.assertEqual(hit['repo'], 'https://github.com/example/tools')
        self.assertEqual(self.run_cache('get', 'https://github.com/example/other')['status'], 'miss')

    def test_expiration_and_refresh(self):
        self.run_cache('put', data=FACTS)
        self.assertEqual(self.run_cache('get', flags=('--refresh',))['status'], 'miss')
        path, = self.root.glob('*.json')
        record = json.loads(path.read_text())
        record['checked_at'] = time.time() - 86_401
        path.write_text(json.dumps(record))
        self.assertEqual(self.run_cache('get')['status'], 'miss')

    def test_broken_storage_degrades_to_uncached_lookup(self):
        self.root.write_text('not a directory')
        self.assertEqual(self.run_cache('get')['status'], 'unavailable')
        self.assertEqual(self.run_cache('put', data=FACTS)['status'], 'unavailable')

    def test_corrupt_entry_is_a_miss_and_can_be_replaced(self):
        self.run_cache('put', data=FACTS)
        path, = self.root.glob('*.json')
        path.write_text('{')
        self.assertEqual(self.run_cache('get')['status'], 'miss')
        self.assertEqual(self.run_cache('put', data=FACTS)['status'], 'stored')

    def test_rejects_urls_with_credentials_or_scope_and_extra_payload(self):
        for repo in ('https://user:secret@github.com/a/b', 'https://github.com/a/b?q=secret',
                     'https://github.com/a/b#secret', 'https://github.com/a/b/tree/main',
                     'https://github.com.evil.test/a/b', 'https://github.com/a/..'):
            with self.subTest(repo=repo):
                self.assertEqual(self.run_cache('put', repo, FACTS)['status'], 'invalid')
        self.assertEqual(self.run_cache('put', data=dict(FACTS, prompt='private'))['status'], 'invalid')
        self.assertEqual(self.run_cache('put', data={'commit': 'a'*40, 'components': [
            {'kind': 'tool', 'path': '../escape'}]})['status'], 'invalid')
        self.assertFalse(self.root.exists())


if __name__ == '__main__':
    unittest.main()
