"""Local, cross-task ToolFinder journal. Python 3.9+, standard library only."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit


def validate(event):
    fields = ('event_id', 'task_id', 'category', 'tool_url', 'kind', 'stage', 'status')
    if not isinstance(event, dict) or any(not isinstance(event.get(k), str) or not event[k].strip() for k in fields):
        raise ValueError('required event fields must be nonempty strings')
    if event.get('schema_version') != 1:
        raise ValueError('unsupported schema_version')
    if event['kind'] not in ('skill', 'prompt', 'agent', 'tool') or event['status'] not in ('recommended', 'used', 'rejected'):
        raise ValueError('invalid kind or status')
    url = urlsplit(event['tool_url'])
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query:
        raise ValueError('tool-url must be a public HTTPS source URL without credentials or query parameters')
    if event['status'] == 'used' and event.get('outcome') not in ('success', 'partial', 'failed'):
        raise ValueError('used requires an outcome: success, partial or failed')
    if event['status'] != 'used' and event.get('outcome') is not None:
        raise ValueError('outcome is only allowed for used')


def record(root, event):
    validate(event)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / (hashlib.sha256(event['event_id'].encode()).hexdigest() + '.json')
    # Exclusive creation prevents retries or concurrent writers overwriting an event.
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        previous = json.loads(path.read_text(encoding='utf-8'))
        if {k: v for k, v in previous.items() if k != 'timestamp'} != event:
            raise ValueError('event-id already exists with different data; inspect the existing event')
        return {'result': 'already_recorded', 'path': str(path)}
    with os.fdopen(fd, 'w', encoding='utf-8') as out:
        json.dump(dict(event, timestamp=datetime.now(timezone.utc).isoformat()), out, ensure_ascii=False, indent=2)
        out.write('\n')
    return {'result': 'recorded', 'path': str(path)}


def summary(root):
    if root.exists() and not root.is_dir():
        raise ValueError("log-dir must be a directory")
    events = []
    for path in sorted(root.glob('*.json')):
        try:
            event = json.loads(path.read_text(encoding='utf-8'))
            validate(event)
            datetime.fromisoformat(event['timestamp'])
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f'invalid journal entry {path.name}: {exc}') from exc
        events.append(event)
    groups = defaultdict(list)
    for event in events:
        groups[event['tool_url']].append(event)
    tools = []
    for url, rows in groups.items():
        # One latest observed use per task/tool. Recommendations never erase a use.
        used = {}
        for row in sorted(rows, key=lambda r: (datetime.fromisoformat(r['timestamp']), r['event_id'])):
            if row['status'] == 'used':
                used[row['task_id']] = row
        tools.append({'tool_url': url, 'used_tasks': len(used),
                      'recommended_tasks': len({r['task_id'] for r in rows if r['status'] == 'recommended'}),
                      'outcomes': dict(Counter(r['outcome'] for r in used.values())),
                      'categories': dict(Counter(r['category'] for r in used.values())),
                      'stages': dict(Counter(r['stage'] for r in used.values()))})
    tools.sort(key=lambda r: (-r['used_tasks'], r['tool_url']))
    return {'log_dir': str(root), 'event_count': len(events), 'tools': tools, 'events': events}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log-dir', default=os.environ.get('TOOL_FINDER_LOG_DIR', '~/.local/share/personal-harness/tool-finder'))
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('summary', help='JSON report, ranked by distinct tasks with actual use')
    add = sub.add_parser('record', help='Record one observed event; reuse event-id for a retry')
    for key in ('event-id', 'task-id', 'category', 'tool-url', 'kind', 'stage', 'status'):
        add.add_argument('--' + key, required=True)
    add.add_argument('--outcome')
    add.add_argument('--note', default='', help='Short sanitized reason or result, no private task content')
    args = vars(parser.parse_args())
    root = Path(args.pop('log_dir')).expanduser().resolve()
    command = args.pop('command')
    try:
        result = summary(root) if command == 'summary' else record(root, dict(args, schema_version=1))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f'Journal error: {exc}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
