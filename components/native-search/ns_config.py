"""Private runtime configuration and verified root identities."""
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path


def xdg(name, fallback):
    return Path(os.environ.get(name, str(Path.home() / fallback)))


CONFIG = xdg('XDG_CONFIG_HOME', '.config') / 'nda-omarchy-stack/native-search/config.json'
DATA = xdg('XDG_DATA_HOME', '.local/share') / 'nda-omarchy-stack/native-search'
STATE = xdg('XDG_STATE_HOME', '.local/state') / 'nda-omarchy-stack/native-search'


def private_dir(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def write_json(path, value):
    private_dir(path.parent)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.write-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2, ensure_ascii=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path, default=None):
    if not path.exists():
        return default
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('JSON file exceeds 1MiB safety bound')
    return json.loads(path.read_text())


def mount_identity(path):
    data = json.loads(subprocess.check_output(
        ['findmnt', '-J', '-T', str(path), '-o', 'TARGET,SOURCE,FSTYPE,UUID'], timeout=3))
    fs = data['filesystems'][0]
    return {key: fs.get(key) for key in ('target', 'fstype', 'uuid', 'source')}


def available(root):
    try:
        path = Path(root['path'])
        if not path.is_dir() or not os.access(path, os.R_OK | os.X_OK):
            return False
        expected = root.get('mount')
        if expected:
            actual = mount_identity(path)
            keys = ('target', 'fstype', 'uuid') if expected.get('uuid') else ('target', 'fstype', 'source')
            if any(expected.get(k) != actual.get(k) for k in keys):
                return False
        return True
    except (OSError, ValueError, subprocess.SubprocessError, KeyError):
        return False


def defaults():
    home = str(Path.home())
    roots = [{'id': 'home', 'path': home, 'enabled': True, 'mount': mount_identity(home)}]
    return {'version': 1, 'roots': roots, 'prunenames': ['.cache', '.Trash', '.Trash-1000',
            'node_modules', '.git'], 'prunepaths': [str(DATA), str(STATE), home + '/.local/share/Trash'],
            'prunefs': ['proc', 'sysfs', 'devtmpfs', 'tmpfs'], 'refresh_seconds': 3600,
            'candidate_budget': 5000, 'result_budget': 100, 'timeout_seconds': 3,
            'kind': 'all', 'extension': '', 'search_mode': 'literal', 'fuzzy_tolerance': 1}


def validate(config):
    if not isinstance(config, dict) or type(config.get('version')) is not int or config['version'] != 1:
        raise ValueError('Unsupported settings schema; existing settings were not overwritten')
    if len(json.dumps(config, ensure_ascii=True)) > 65536:
        raise ValueError('Settings exceed 64KiB safety bound')
    roots = config.get('roots')
    if not isinstance(roots, list) or not 1 <= len(roots) <= 16:
        raise ValueError('Settings require 1–16 explicit roots')
    ids, paths = set(), []
    for root in roots:
        if not isinstance(root, dict) or not isinstance(root.get('id'), str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', root['id']):
            raise ValueError('Invalid root ID')
        path = root.get('path', '')
        if not isinstance(path, str) or not os.path.isabs(path) or '\0' in path or len(os.fsencode(path)) > 4096:
            raise ValueError('Root paths must be absolute')
        path = os.path.realpath(path)
        if root['id'] in ids or any(os.path.commonpath([path, p]) in (path, p) for p in paths):
            raise ValueError('Duplicate IDs or overlapping roots')
        root['path'] = path
        if not isinstance(root.get('enabled'), bool):
            raise ValueError('Root enabled must be boolean')
        if not isinstance(root.get('mount'), dict):
            raise ValueError('Each root requires an explicit expected mount identity')
        ids.add(root['id'])
        paths.append(path)
    for key in ('prunenames', 'prunepaths', 'prunefs'):
        values = config.get(key)
        if not isinstance(values, list) or len(values) > 128 or any(
                not isinstance(v, str) or not v or '\0' in v or
                (not os.path.isabs(v) if key == 'prunepaths' else any(c.isspace() for c in v)) for v in values):
            raise ValueError('Invalid exclusions: ' + key)
    for key, low, high in [('refresh_seconds', 300, 86400), ('candidate_budget', 100, 10000),
                           ('result_budget', 1, 500), ('timeout_seconds', 1, 10)]:
        if type(config.get(key)) is not int or not low <= config[key] <= high:
            raise ValueError('Invalid bounded setting: ' + key)
    if config.get('kind') not in ('all', 'files', 'folders') or not isinstance(config.get('extension'), str) or len(config['extension']) > 32:
        raise ValueError('Invalid filter defaults')
    config.setdefault('search_mode', 'literal')
    config.setdefault('fuzzy_tolerance', 1)
    if config['search_mode'] not in ('literal', 'fuzzy'):
        raise ValueError('Search mode must be literal or fuzzy')
    if type(config['fuzzy_tolerance']) is not int or not 0 <= config['fuzzy_tolerance'] <= 2:
        raise ValueError('Fuzzy tolerance must be 0–2')
    return config


def load():
    value = read_json(CONFIG)
    if value is None:
        raise ValueError('Not configured; run search install first')
    return validate(value)
