"""Bounded NUL-delimited literal queries; lossless opaque result identity."""
import hashlib
import json
import os
import stat
import subprocess
import time
from ns_config import DATA, available
from ns_index import metadata, scope_fingerprint, submounts
from ns_fuzzy import candidate_pattern, rank as fuzzy_rank


def literal(term):
    return '*' + ''.join({'*': '[*]', '?': '[?]', '[': '[[]', '\\': '\\\\'}.get(c, c) for c in term) + '*'


def display(path):
    text = ''.join({'\n': r'\n', '\r': r'\r', '\t': r'\t'}.get(c,
                   '\\x%02x' % ord(c) if ord(c) < 32 or ord(c) == 127 else c) for c in os.fsdecode(path))
    return text.encode('utf-8', 'backslashreplace').decode('utf-8')


class Search:
    def __init__(self):
        self.identities = {}

    def query(self, config, args, cancel):
        started = time.monotonic()
        query = args.get('query', '')
        if not isinstance(query, str) or len(query) > 512 or '\0' in query:
            raise ValueError('Query must be at most 512 characters without NUL')
        terms = query.split()
        mode = args.get('mode', config.get('search_mode', 'literal'))
        tolerance = config.get('fuzzy_tolerance', 1)
        if mode not in ('literal', 'fuzzy'):
            raise ValueError('Unknown search mode')
        if len(terms) > 32:
            raise ValueError('At most 32 search terms')
        kind = args.get('kind', config['kind'])
        extension = args.get('extension', config['extension'])
        if not isinstance(extension, str):
            raise ValueError('Extension must be text')
        extension = extension.strip().lower().lstrip('.')
        if kind not in ('all', 'files', 'folders') or len(extension) > 32:
            raise ValueError('Invalid filter')
        budget = args.get('limit', config['result_budget'])
        if type(budget) is not int or not 1 <= budget <= 500:
            raise ValueError('Result budget must be 1–500')
        root_ids = args.get('roots', [])
        if not isinstance(root_ids, list) or any(i not in [r['id'] for r in config['roots']] for i in root_ids):
            raise ValueError('Unknown root selection')
        roots = [r for r in config['roots'] if r['enabled'] and (not root_ids or r['id'] in root_ids)]
        rows, identities, errors, generations = [], {}, [], {}
        partial = False
        if not terms:
            self.identities = {}
            return {'results': [], 'partial': False, 'duration_ms': 0, 'generations': {}}
        allocation = max(1, config['candidate_budget'] // max(1, len(roots)))
        seen = set()
        for root in roots:
            if cancel.is_set():
                raise InterruptedError('Search cancelled')
            if not available(root):
                errors.append(root['id'] + ': offline or identity changed')
                continue
            info = metadata(root)
            if not info.get('database'):
                errors.append(root['id'] + ': build index first')
                continue
            if info.get('scope') != scope_fingerprint(config, root):
                errors.append(root['id'] + ': scope/exclusions changed; rebuild index')
                continue
            excluded_mounts = [os.fsencode(p.rstrip('/') + '/') for p in submounts(root)]
            generations[root['id']] = info['generation']
            cmd = ['plocate', '-d', str(DATA / info['database']), '-0', '-i', '-l', str(allocation + 1)]
            cmd += (['--regex', '--', candidate_pattern(terms, tolerance)] if mode == 'fuzzy'
                    else ['--'] + [literal(t) for t in terms])
            with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
                deadline = time.monotonic() + config['timeout_seconds']
                while True:
                    try:
                        raw, stderr = proc.communicate(timeout=.025)
                        break
                    except subprocess.TimeoutExpired:
                        if cancel.is_set() or time.monotonic() > deadline:
                            proc.kill()
                            proc.communicate()
                            if cancel.is_set():
                                raise InterruptedError('Search cancelled')
                            raise TimeoutError('Query timed out; narrow the query')
                if proc.returncode not in (0, 1) or stderr:
                    raise ValueError('plocate failed: corrupt/missing database or executable error')
            candidates = raw.split(b'\0')[:-1]
            partial |= len(candidates) > allocation
            for path in candidates[:allocation]:
                if cancel.is_set():
                    raise InterruptedError('Search cancelled')
                full = os.fsdecode(path).casefold()
                if path in seen:
                    continue
                basename = os.fsdecode(os.path.basename(path)).casefold()
                fuzzy_score = fuzzy_rank(terms, basename, os.path.relpath(os.fsdecode(path), root['path']), tolerance) if mode == 'fuzzy' else None
                if (mode == 'fuzzy' and fuzzy_score is None) or (mode == 'literal' and not all(t.casefold() in full for t in terms)):
                    continue
                if not path.startswith(os.fsencode(root['path'].rstrip('/') + '/')):
                    continue
                if any(path == prefix[:-1] or path.startswith(prefix) for prefix in excluded_mounts):
                    continue
                seen.add(path)
                name = os.path.basename(path)
                if extension and not os.fsdecode(name).lower().endswith('.' + extension):
                    continue
                try:
                    mode = os.stat(path, follow_symlinks=False).st_mode
                    item_kind = 'folder' if stat.S_ISDIR(mode) else 'file' if stat.S_ISREG(mode) or stat.S_ISLNK(mode) else 'unknown'
                except OSError:
                    item_kind = 'unknown'
                if kind != 'all' and item_kind != ('folder' if kind == 'folders' else 'file'):
                    continue
                opaque = hashlib.sha256(root['id'].encode() + info['generation'].encode() + path).hexdigest()
                identities[opaque] = (root.copy(), info['generation'], path)
                basename = os.fsdecode(name).casefold()
                needle = query.strip().casefold()
                rank = 0 if basename == needle else 1 if basename.startswith(needle) else 2 if all(t.casefold() in basename for t in terms) else 3
                if mode == 'fuzzy':
                    rank = fuzzy_score
                rows.append((rank, basename, full, {'id': opaque, 'root': root['id'], 'name': display(name),
                    'parent': display(os.path.dirname(path)), 'kind': item_kind,
                    'actionable': item_kind != 'unknown'}))
        rows.sort(key=lambda row: row[:3])
        partial |= len(rows) > budget
        partial |= bool(errors)
        results = []
        encoded_size = 0
        for row in rows[:budget]:
            encoded_size += len(json.dumps(row[3], ensure_ascii=True)) + 2
            if encoded_size > 512 * 1024:
                partial = True
                break
            results.append(row[3])
        if not cancel.is_set():
            self.identities = {r['id']: identities[r['id']] for r in results}
        return {'results': results, 'partial': partial, 'errors': errors, 'shown': len(results),
                'generations': generations, 'duration_ms': round((time.monotonic() - started) * 1000, 2)}
