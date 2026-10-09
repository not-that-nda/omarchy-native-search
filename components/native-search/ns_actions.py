"""Activation-time checks and argv-only desktop/clipboard operations."""
import os
import subprocess
from urllib.parse import quote_from_bytes
from ns_config import available, mount_identity
from ns_index import metadata, scope_fingerprint


def uri(path):
    return 'file://' + quote_from_bytes(path, safe='/')


def selected_path(search, config, args):
    identity = search.identities.get(args.get('id'))
    if identity is None:
        raise ValueError('Selection expired; search again')
    root, generation, path = identity
    current = next((r for r in config['roots'] if r['id'] == root['id']), None)
    if current != root or not root['enabled'] or not available(root):
        raise ValueError('Root unavailable or settings changed; search again')
    info = metadata(root)
    if info.get('scope') != scope_fingerprint(config, root):
        raise ValueError('Index scope changed; rebuild and search again')
    if info.get('generation') != generation:
        raise ValueError('Index generation changed; search again')
    if not os.path.exists(path) or not os.access(path, os.R_OK):
        raise ValueError('File deleted or unreadable; refresh the index')
    parent = os.path.dirname(path)
    if os.path.commonpath([os.path.realpath(parent), os.fsencode(root['path'])]) != os.fsencode(root['path']):
        raise ValueError('Indexed parent moved outside its configured root; search again')
    actual = mount_identity(os.fsdecode(parent if os.path.islink(path) else path))
    expected = root['mount']
    keys = ('target', 'fstype', 'uuid') if expected.get('uuid') else ('target', 'fstype', 'source')
    if any(actual.get(key) != expected.get(key) for key in keys):
        raise ValueError('File is on an unexpected subordinate mount; refresh the index')
    return path


def action(search, config, args):
    path = selected_path(search, config, args)
    operation = args.get('action')
    target_uri = uri(path)
    if operation in ('copy-path', 'copy-uri'):
        payload = path if operation == 'copy-path' else target_uri.encode()
        subprocess.run(['wl-copy', '--type', 'text/plain;charset=utf-8'], input=payload, check=True, timeout=3,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {'message': 'Copied path' if operation == 'copy-path' else 'Copied file URI'}
    if operation == 'open':
        subprocess.Popen(['xdg-open', target_uri], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return {'message': 'Open requested with desktop default'}
    if operation == 'reveal':
        proc = subprocess.run(['gdbus', 'call', '--session', '--dest', 'org.freedesktop.FileManager1',
            '--object-path', '/org/freedesktop/FileManager1', '--method', 'org.freedesktop.FileManager1.ShowItems',
            "['" + target_uri.replace("'", '%27') + "']", ''], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=3)
        if proc.returncode == 0:
            return {'message': 'Revealed in file manager'}
        subprocess.Popen(['xdg-open', uri(os.path.dirname(path))], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return {'message': 'Opened parent; file selection unavailable'}
    raise ValueError('Unknown action')
