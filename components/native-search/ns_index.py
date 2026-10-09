"""Serialized, low-priority, atomic per-root plocate generations."""
import fcntl
import hashlib
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from ns_config import DATA, available, private_dir, read_json, write_json


def metadata(root):
    return read_json(DATA / (root['id'] + '.json'), {})


def scope_fingerprint(config, root):
    scope = {'path': root['path'], 'mount': root['mount']}
    scope.update({key: sorted(set(config[key])) for key in ('prunenames', 'prunepaths', 'prunefs')})
    return hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()


def submounts(root):
    mounts = json.loads(subprocess.check_output(['findmnt', '-J', '-l', '-o', 'TARGET'], timeout=3))['filesystems']
    return sorted(m['target'] for m in mounts if m['target'] != root['path'] and
                  m['target'].startswith(root['path'].rstrip('/') + '/'))


def status(config):
    now = time.time()
    output = []
    for root in config['roots']:
        info = metadata(root)
        online = root['enabled'] and available(root)
        info = dict(info, id=root['id'], path=root['path'], enabled=root['enabled'], online=online)
        info['age_seconds'] = int(now - info['last_success']) if info.get('last_success') else None
        info['stale'] = info['age_seconds'] is None or info['age_seconds'] > 2 * config['refresh_seconds']
        info['scope_changed'] = bool(info.get('database') and info.get('scope') != scope_fingerprint(config, root))
        info['indexed'] = bool(info.get('database') and (DATA / info['database']).is_file() and not info['scope_changed'])
        info['stale'] |= not info['indexed']
        lock_path = DATA / (root['id'] + '.lock')
        if lock_path.exists():
            with lock_path.open('a') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    info['building'] = False
                except BlockingIOError:
                    info['building'] = True
        if not info.get('building') and info.get('last_attempt', 0) > info.get('last_success', 0) and not info.get('error'):
            info['error'] = 'Interrupted refresh; last successful snapshot retained'
        output.append(info)
    return output


def on_battery():
    for path in Path('/sys/class/power_supply').glob('*/type'):
        if path.read_text().strip() == 'Battery':
            state = path.parent / 'status'
            if state.exists() and state.read_text().strip() == 'Discharging':
                return True
    return False


def refresh_root(config, root, scheduled=False):
    private_dir(DATA)
    with (DATA / (root['id'] + '.lock')).open('a') as lock:
        os.fchmod(lock.fileno(), 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'id': root['id'], 'status': 'already-building'}
        info = metadata(root)
        if not root['enabled']:
            return {'id': root['id'], 'status': 'disabled'}
        if scheduled and (not info.get('last_success') or info.get('scope') != scope_fingerprint(config, root)):
            return {'id': root['id'], 'status': 'needs-first-manual-build'}
        if scheduled and time.time() - info.get('last_success', 0) < config['refresh_seconds']:
            return {'id': root['id'], 'status': 'fresh'}
        if scheduled and on_battery():
            info['deferred'] = 'Battery: automatic refresh deferred'
            write_json(DATA / (root['id'] + '.json'), info)
            return {'id': root['id'], 'status': 'deferred-battery'}
        started = time.time()
        info.update(last_attempt=started, error=None, deferred=None)
        write_json(DATA / (root['id'] + '.json'), info)
        generation = uuid.uuid4().hex
        filename = root['id'] + '-' + generation + '.db'
        temporary = DATA / ('.' + filename)
        try:
            if not available(root):
                raise ValueError('Root offline, unreadable or mount identity changed')
            # Explicit root overrides prevent /run pruning from hiding Storage.
            # All pruning values are overridden explicitly below. Avoid --config-file,
            # which is unavailable in plocate 1.1.19 (Ubuntu's CI package).
            cmd = ['nice', '-n', '15', 'ionice', '-c', '3', 'updatedb',
                   '-U', root['path'], '-o', str(temporary), '--require-visibility', 'no',
                   '--prune-bind-mounts', 'yes', '--prunefs', ' '.join(config['prunefs']),
                   '--prunenames', ' '.join(config['prunenames']), '--prunepaths', '']
            for path in config['prunepaths']:
                cmd += ['--add-single-prunepath', path]
            pruned_mounts = submounts(root)
            for target in pruned_mounts:
                cmd += ['--add-single-prunepath', target]
            # Quiet updatedb: never write private filename inventories into logs.
            done = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=7200)
            if done.returncode:
                raise ValueError('updatedb failed (exit %s); previous snapshot retained' % done.returncode)
            probe = subprocess.run(['plocate', '-d', str(temporary), '-c', '--', '/'],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            if probe.returncode not in (0, 1) or probe.stderr or not available(root) or submounts(root) != pruned_mounts:
                raise ValueError('Index validation or final mount identity check failed')
            temporary.chmod(0o600)
            os.replace(temporary, DATA / filename)
            old = info.get('database')
            published = dict(info, database=filename, generation=generation, last_success=time.time(),
                        duration_seconds=round(time.time() - started, 3), bytes=(DATA / filename).stat().st_size,
                        mount=root['mount'], root_path=root['path'], scope=scope_fingerprint(config, root),
                        indexed_paths=int(probe.stdout.strip()), error=None)
            write_json(DATA / (root['id'] + '.json'), published)
            info = published
            # Keep one prior generation so in-flight readers cannot race publication.
            keep = {filename, old}
            for path in DATA.glob(root['id'] + '-*.db'):
                if path.name not in keep:
                    path.unlink()
            return {'id': root['id'], 'status': 'updated', **info}
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            unpublished = DATA / filename
            if info.get('database') != filename and unpublished.exists():
                unpublished.unlink()
            info['error'] = str(error)
            info['duration_seconds'] = round(time.time() - started, 3)
            write_json(DATA / (root['id'] + '.json'), info)
            return {'id': root['id'], 'status': 'failed', **info}
        finally:
            temporary.unlink(missing_ok=True)


def refresh(config, root_ids=None, scheduled=False):
    return [refresh_root(config, r, scheduled) for r in config['roots']
            if root_ids is None or r['id'] in root_ids]
