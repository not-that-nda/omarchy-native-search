"""Parent CLI component lifecycle, narrow ownership and conflict-safe removal."""
import argparse
import hashlib
import fcntl
from functools import wraps
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from ns_config import CONFIG, STATE, defaults, load, private_dir, read_json, validate, write_json, xdg
from ns_index import status

SOURCE = Path(__file__).resolve().parent
ID = 'nda.native-search'
DEPENDENCIES = ['python3', 'plocate', 'updatedb', 'findmnt', 'nice', 'ionice', 'wl-copy',
                'xdg-open', 'gdbus', 'quickshell', 'omarchy-shell', 'omarchy-hyprland-session-locked']
SHORTCUT = 'SUPER + B'
BLOCK = ('\n-- BEGIN nda-native-search\n'
         '-- Preserve the earlier split binding for removal/rollback.\n'
         'hl.unbind("SUPER + B")\n'
         'o.bind("' + SHORTCUT + '", "Native Search", '
         '"omarchy-shell shell toggle nda.native-search \'{}\'")\n'
         '-- END nda-native-search\n')


def serialized(method):
    @wraps(method)
    def wrapped(*args, **kwargs):
        private_dir(STATE)
        with (STATE / 'lifecycle.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('Another Native Search lifecycle operation is active')
            return method(*args, **kwargs)
    return wrapped


def digest(data):
    return hashlib.sha256(data).hexdigest()


def ipc(*args):
    return subprocess.check_output(['omarchy-shell', 'shell', *args],
        env=os.environ.copy(), text=True, timeout=10).strip()


class Component:
    def __init__(self):
        self.config_dir = xdg('XDG_CONFIG_HOME', '.config')
        self.plugin = self.config_dir / 'omarchy/plugins' / ID
        self.shell = self.config_dir / 'omarchy/shell.json'
        self.bindings = self.config_dir / 'hypr/bindings.lua'
        self.units = self.config_dir / 'systemd/user'
        self.receipt = STATE / 'receipt.json'

    def payload(self):
        files = {self.plugin / p.name: p.read_bytes() for p in SOURCE.iterdir()
                 if p.suffix == '.py' and p.name != 'lifecycle.py'}
        qml = (SOURCE / 'Search.qml').read_bytes()
        # This host retains failed Qt component loads by URL across rescans.
        # Content-addressing gives upgrades a fresh URL without a shell restart.
        entry = 'ui' + digest(qml)[:12] + '/Search.qml'
        files[self.plugin / entry] = qml
        manifest = read_json(SOURCE / 'manifest.json')
        manifest['entryPoints']['menu'] = entry
        files[self.plugin / 'manifest.json'] = (json.dumps(manifest, indent=2) + '\n').encode()
        helper = self.plugin / 'helper.py'
        service = ('[Unit]\nDescription=Native Search private index refresh\n'
                   '[Service]\nType=oneshot\nUMask=0077\nNice=15\nIOSchedulingClass=idle\n'
                   'ExecStart=/usr/bin/python3 -B "' + str(helper) + '" refresh\n')
        scheduled = service.replace('" refresh\n', '" refresh --scheduled\n')
        files[self.units / 'nda-native-search-refresh.service'] = service.encode()
        files[self.units / 'nda-native-search-scheduled.service'] = scheduled.encode()
        files[self.units / 'nda-native-search.timer'] = (
            '[Unit]\nDescription=Native Search hourly stale-index eligibility\n'
            '[Timer]\nOnStartupSec=10min\nOnUnitInactiveSec=1h\nRandomizedDelaySec=5min\n'
            'Persistent=false\nUnit=nda-native-search-scheduled.service\n'
            '[Install]\nWantedBy=timers.target\n').encode()
        return files

    def preview(self):
        return {'status': 'installed' if self.receipt.exists() else 'not-installed',
                'missing_dependencies': [d for d in DEPENDENCIES if not shutil.which(d)],
                'shortcut': SHORTCUT, 'files': [str(p) for p in self.payload()],
                'config': str(CONFIG), 'scope': 'Explicit home plus verified Storage mount; settings show exclusions',
                'timer': 'hourly eligibility, battery deferral; no persistent catch-up bursts'}

    def check_shell(self):
        config = read_json(self.shell)
        if not isinstance(config, dict) or config.get('version') != 1 or not isinstance(config.get('plugins'), list):
            raise ValueError('Expected existing version-1 Omarchy shell.json with plugins list')
        if not self.bindings.is_file():
            raise ValueError('Expected existing Hyprland bindings.lua')
        return config

    @serialized
    def install(self, native=True):
        missing = self.preview()['missing_dependencies']
        if missing:
            raise ValueError('Missing dependencies: ' + ', '.join(missing))
        shell_config = self.check_shell()
        existing = read_json(self.receipt)
        if existing and existing.get('status') != 'installed':
            raise ValueError('Interrupted lifecycle receipt; reconcile owned files before retry')
        payload = self.payload()
        config = load() if CONFIG.exists() else validate(defaults())
        bindings = self.bindings.read_text()
        if existing:
            self.verify(existing)
        else:
            if any(p.exists() for p in payload):
                raise ValueError('Foreign Native Search payload exists; refusing to adopt or overwrite')
            if '-- BEGIN nda-native-search' in bindings:
                raise ValueError('Foreign Native Search binding exists')
            if any(e.get('id') == ID for e in shell_config['plugins']):
                raise ValueError('Foreign plugin registration exists')
        if native and (not existing or existing['block'] != BLOCK):
            effective = json.loads(subprocess.check_output(['hyprctl', '-j', 'binds'], timeout=3))
            conflicts = [b for b in effective if b['modmask'] == 64 and b['key'].upper() == 'B'
                         and b.get('submap', '') == '']
            if any(b.get('description') != 'Toggle window split' for b in conflicts):
                raise ValueError('Super+B has an unexpected action; reconcile before replacement')
        receipt = existing or {'version': 1, 'status': 'installing', 'files': {},
            'shell_path': str(self.shell.resolve()), 'bindings_path': str(self.bindings.resolve()),
            'block': BLOCK, 'entry': {'id': ID}, 'timer_enabled_before': False}
        private_dir(STATE)
        if not existing:
            # Byte-exact private backups are outside distributable source.
            shutil.copy2(self.shell, STATE / 'shell.before.json')
            shutil.copy2(self.bindings, STATE / 'bindings.before.lua')
            write_json(self.receipt, receipt)
        elif existing['block'] != BLOCK or any(not p.exists() or p.read_bytes() != data for p, data in payload.items()):
            write_json(STATE / 'receipt.previous.json', existing)
            receipt['status'] = 'installing'
            write_json(self.receipt, receipt)
        for name in ('shell.before.json', 'bindings.before.lua'):
            backup = STATE / name
            if backup.exists():
                backup.chmod(0o600)
        changed = 0
        old_files = dict(receipt['files'])
        receipt['files'] = {}
        for path, data in payload.items():
            if not path.exists() or path.read_bytes() != data:
                private_dir(path.parent)
                path.write_bytes(data)
                path.chmod(0o600)
                changed += 1
            receipt['files'][str(path)] = digest(data)
        for name in old_files:
            if Path(name) not in payload:
                Path(name).unlink()
                changed += 1
        if BLOCK not in bindings:
            shutil.copy2(self.bindings, STATE / 'bindings.before-upgrade.lua')
            (STATE / 'bindings.before-upgrade.lua').chmod(0o600)
            self.bindings.write_text(bindings.replace(receipt['block'], BLOCK, 1)
                                     if existing else bindings + BLOCK)
            changed += 1
        receipt['block'] = BLOCK
        if receipt['entry'] not in shell_config['plugins']:
            shell_config['plugins'].append(receipt['entry'])
            write_json(self.shell.resolve(), shell_config)
            changed += 1
        if not CONFIG.exists():
            write_json(CONFIG, config)
            changed += 1
        receipt['status'] = 'installed'
        if read_json(self.receipt) != receipt:
            write_json(self.receipt, receipt)
        if native and changed:
            subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
            subprocess.run(['systemctl', '--user', 'enable', '--now', 'nda-native-search.timer'], check=True,
                           stdout=subprocess.DEVNULL)
            ipc('rescanPlugins')
            subprocess.run(['hyprctl', 'reload'], check=True, stdout=subprocess.DEVNULL)
            errors = subprocess.check_output(['hyprctl', 'configerrors'], text=True).strip()
            if errors:
                raise ValueError('Hyprland configerrors: ' + errors)
        return {'status': 'installed', 'files_changed': changed, 'shortcut': SHORTCUT,
                'note': 'No index scan on install; explicitly run search refresh for the first build'}

    def verify(self, receipt):
        if str(self.shell.resolve()) != receipt['shell_path'] or str(self.bindings.resolve()) != receipt['bindings_path']:
            raise ValueError('Configuration link target changed; reconcile before mutation')
        for name, expected in receipt['files'].items():
            path = Path(name)
            if not path.is_file() or digest(path.read_bytes()) != expected:
                raise ValueError('Managed payload changed or disappeared; preserve and reconcile: ' + name)
        if self.bindings.read_text().count(receipt['block']) != 1:
            raise ValueError('Managed binding changed; refusing to discard user edits')
        entries = read_json(self.shell)['plugins']
        owned = [e for e in entries if e.get('id') == ID]
        if owned != [receipt['entry']]:
            raise ValueError('Managed plugin entry changed; refusing to discard user edits')

    @serialized
    def remove(self, native=True):
        receipt = read_json(self.receipt)
        if not receipt:
            return {'status': 'not-installed'}
        if receipt.get('status') != 'installed':
            raise ValueError('Interrupted lifecycle receipt; reconcile first')
        self.verify(receipt)
        if native:
            ipc('hide', ID)
            subprocess.run(['systemctl', '--user', 'disable', '--now', 'nda-native-search.timer'], check=True)
            subprocess.run(['systemctl', '--user', 'stop', 'nda-native-search-refresh.service',
                            'nda-native-search-scheduled.service'], check=True)
        shell_config = read_json(self.shell)
        shell_config['plugins'].remove(receipt['entry'])
        write_json(self.shell.resolve(), shell_config)
        self.bindings.write_text(self.bindings.read_text().replace(receipt['block'], '', 1))
        for name in receipt['files']:
            Path(name).unlink()
        # Python may have created bytecode here; leaving an empty plugin directory is harmless.
        self.receipt.unlink()
        if native:
            subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
            ipc('rescanPlugins')
            subprocess.run(['hyprctl', 'reload'], check=True, stdout=subprocess.DEVNULL)
            errors = subprocess.check_output(['hyprctl', 'configerrors'], text=True).strip()
            if errors:
                raise ValueError(errors)
        return {'status': 'removed', 'retained': 'User config, indexes and private original backups'}

    def doctor(self):
        receipt = read_json(self.receipt)
        if not receipt:
            raise ValueError('Component not installed')
        self.verify(receipt)
        missing = self.preview()['missing_dependencies']
        if missing:
            raise ValueError('Missing dependencies: ' + ', '.join(missing))
        return {'status': 'ready', 'shortcut': SHORTCUT, 'roots': status(load()),
                'shell_ping': ipc('ping'), 'timer_active': subprocess.check_output(
                    ['systemctl', '--user', 'is-active', 'nda-native-search.timer'], text=True).strip()}


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description='Native Search lifecycle and invocation')
    parser.add_argument('action', choices=['plan', 'install', 'remove', 'doctor', 'toggle', 'show', 'hide',
                                          'status', 'refresh'])
    args = parser.parse_args(argv)
    component = Component()
    if args.action == 'plan':
        result = component.preview()
    elif args.action in ('install', 'remove', 'doctor'):
        result = getattr(component, args.action)()
    elif args.action in ('toggle', 'show', 'hide'):
        result = {'status': ipc('summon' if args.action == 'show' else args.action, ID,
                               *([] if args.action == 'hide' else ['{}']))}
    elif args.action == 'status':
        result = {'roots': status(load())}
    else:
        subprocess.run(['systemctl', '--user', 'start', '--no-block', 'nda-native-search-refresh.service'], check=True)
        result = {'status': 'refresh-requested', 'note': 'Use search status for progress and last-success age'}
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
