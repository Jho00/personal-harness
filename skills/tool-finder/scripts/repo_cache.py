"""Opt-in local snapshots of public GitHub component inventories. Python 3.9+."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time

TTL_SECONDS = 24 * 60 * 60
MAX_BYTES = 256 * 1024
ORIGIN = re.compile(r'https://github\.com/([a-z0-9-]{1,39})/([a-z0-9_.-]{1,104})/?', re.ASCII | re.I)
COMPONENT_PATH = re.compile(r'[A-Za-z0-9_. /-]{1,512}')
KINDS = {'skill', 'prompt', 'agent', 'tool'}


def canonical_repo(raw):
    match = ORIGIN.fullmatch(raw)
    if not match:
        raise ValueError('expected HTTPS GitHub repository root without credentials, path suffix or parameters')
    owner, repo = (part.lower() for part in match.groups())
    if repo.endswith('.git'):
        repo = repo[:-4]
    if repo in ('', '.', '..') or len(repo) > 100:
        raise ValueError('invalid repository name')
    return f'https://github.com/{owner}/{repo}'


def validate_facts(facts):
    if not isinstance(facts, dict) or set(facts) != {'commit', 'components'}:
        raise ValueError('expected commit and components only')
    if not isinstance(facts['commit'], str) or not re.fullmatch(r'[a-f0-9]{40}', facts['commit']):
        raise ValueError('expected full lowercase Git commit SHA')
    items = facts['components']
    if not isinstance(items, list) or len(items) > 250:
        raise ValueError('expected a list of up to 250 components')
    for item in items:
        if not isinstance(item, dict) or set(item) != {'kind', 'path'}:
            raise ValueError('expected only kind and path for each component')
        kind, path = item['kind'], item['path']
        if not isinstance(kind, str) or kind not in KINDS or not isinstance(path, str):
            raise ValueError('invalid component kind or path')
        if not COMPONENT_PATH.fullmatch(path) or any(
            part in ('', '.', '..') or part != part.strip() for part in path.split('/')
        ):
            raise ValueError('expected repository-relative component path')


def get(path, repo, refresh):
    if refresh:
        return {'status': 'miss', 'reason': 'refresh'}
    try:
        with path.open('rb') as stream:
            data = stream.read(MAX_BYTES + 1)
    except FileNotFoundError:
        return {'status': 'miss', 'reason': 'absent'}
    except OSError:
        return {'status': 'unavailable'}
    try:
        if len(data) > MAX_BYTES:
            raise ValueError('oversized entry')
        record = json.loads(data)
        if not isinstance(record, dict) or record.get('version') != 1 or record.get('repo') != repo:
            raise ValueError('mismatched entry')
        validate_facts(record.get('facts'))
        checked = record.get('checked_at')
        if type(checked) not in (int, float) or not 0 <= time.time() - checked < TTL_SECONDS:
            return {'status': 'miss', 'reason': 'expired'}
    except (ValueError, RecursionError, OverflowError):
        return {'status': 'miss', 'reason': 'invalid_entry'}
    return {'status': 'hit', 'repo': repo, 'checked_at': checked, 'facts': record['facts']}


def put(path, repo):
    raw = sys.stdin.buffer.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('oversized input')
    facts = json.loads(raw)
    validate_facts(facts)
    data = json.dumps({'version': 1, 'repo': repo, 'checked_at': time.time(), 'facts': facts})
    temp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            temp = Path(stream.name)
            stream.write(data)
        os.replace(temp, path)
    except OSError:
        return {'status': 'unavailable'}
    finally:
        if temp is not None:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
    return {'status': 'stored', 'repo': repo}


def main():
    base = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-dir', type=Path, default=Path(
        os.environ.get('TOOL_FINDER_CACHE_DIR') or base / 'personal-harness' / 'tool-finder' / 'repos'
    ))
    commands = parser.add_subparsers(dest='command', required=True)
    read = commands.add_parser('get', help='read a fresh local repository snapshot')
    read.add_argument('repo')
    read.add_argument('--refresh', action='store_true', help='ignore any stored snapshot')
    write = commands.add_parser('put', help='store public, task-independent facts from JSON stdin')
    write.add_argument('repo')
    args = parser.parse_args()
    try:
        repo = canonical_repo(args.repo)
        path = args.cache_dir.expanduser() / (hashlib.sha256(repo.encode()).hexdigest() + '.json')
        result = get(path, repo, args.refresh) if args.command == 'get' else put(path, repo)
    except (ValueError, RecursionError, RuntimeError):
        result = {'status': 'invalid'}
    print(json.dumps(result))


if __name__ == '__main__':
    main()
