"""Standalone distribution adapter; reuses the component's ownership lifecycle."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import lifecycle as core
from ns_config import CONFIG, STATE, defaults, load, mount_identity, read_json, validate, write_json

APP = 'omarchy-native-search'
MARKER = '-- BEGIN omarchy-native-search'


def shortcut_value(value):
    if value is None or value == 'none':
        return None
    parts = [p.strip().upper() for p in value.split('+')]
    mods = {'SUPER': 64, 'CTRL': 4, 'SHIFT': 1, 'ALT': 8}
    if len(parts) < 2 or len(set(parts[:-1])) != len(parts[:-1]) or any(p not in mods for p in parts[:-1]) or not re.fullmatch(r'[A-Z0-9]+', parts[-1]):
        raise ValueError('Shortcut example: SUPER+CTRL+F; or none')
    return ' + '.join(parts)


def binding(shortcut):
    line = '' if not shortcut else 'o.bind(' + json.dumps(shortcut) + ', "Native Search", "omarchy-shell shell toggle nda.native-search \'{}\'")\n'
    return '\n' + MARKER + '\n' + line + '-- END omarchy-native-search\n'


def roots_config(values):
    config = defaults()
    # This public package never assumes an author-specific Storage mount.
    config['roots'] = []
    for number, value in enumerate(values or [str(Path.home())]):
        path = Path(value).expanduser().resolve()
        if not path.is_dir():
            raise ValueError('Index root must be an existing directory: ' + str(path))
        config['roots'].append({'id': 'home' if path == Path.home() else 'root' + str(number + 1),
                                'path': str(path), 'enabled': True, 'mount': mount_identity(path)})
    return validate(config)


class Distribution(core.Component):
    def __init__(self, shortcut=None):
        super().__init__()
        self.bin = Path(os.environ.get('XDG_BIN_HOME', str(Path.home() / '.local/bin'))) / APP
        receipt = read_json(self.receipt)
        if receipt and receipt.get('distribution') != APP:
            raise ValueError('A different Native Search installation owns this state; do not overwrite it')
        self.shortcut = shortcut_value(shortcut) if shortcut is not None else (receipt or {}).get('shortcut')
        core.SHORTCUT = self.shortcut
        core.BLOCK = binding(self.shortcut)

    def payload(self):
        files = super().payload()
        # Retain source entrypoints so the installed CLI is independent of the checkout.
        for name in ('lifecycle.py', 'public_cli.py', 'Search.qml'):
            files[self.plugin / name] = (core.SOURCE / name).read_bytes()
        files[self.bin] = ('#!/bin/bash\nexec /usr/bin/python3 -B ' + shlex.quote(str(self.plugin / 'public_cli.py')) + ' "$@"\n').encode()
        return files

    def preview(self):
        result = super().preview()
        result['scope'] = 'Home by default; --root selects explicit local directories'
        result['version'] = read_json(core.SOURCE / 'manifest.json')['version']
        result['optional_previews'] = {'pdf': bool(shutil.which('pdftoppm')),
                                       'images': __import__('importlib.util', fromlist=['find_spec']).find_spec('PIL') is not None}
        return result

    def check_shortcut(self):
        if not self.shortcut:
            return
        parts = self.shortcut.split(' + ')
        masks = {'SUPER': 64, 'CTRL': 4, 'SHIFT': 1, 'ALT': 8}
        mask = sum(masks[p] for p in parts[:-1])
        existing = read_json(self.receipt) or {}
        for row in json.loads(subprocess.check_output(['hyprctl', '-j', 'binds'], timeout=3)):
            if row['modmask'] == mask and row['key'].upper() == parts[-1] and not row.get('submap'):
                if existing.get('shortcut') != self.shortcut or row.get('description') != 'Native Search':
                    raise ValueError('Shortcut already occupied; choose another or --shortcut none')

    @core.serialized
    def install(self, native=True, roots=None):
        if roots and CONFIG.exists():
            raise ValueError('Existing configuration preserved; use configure --root to change scope')
        if native:
            if not os.environ.get('OMARCHY_PATH'):
                raise ValueError('Run inside an Omarchy graphical session with OMARCHY_PATH set')
            self.check_shortcut()
        if not CONFIG.exists():
            # Validate before the base lifecycle writes any payload.
            initial = roots_config(roots)
        else:
            initial = load()
        original_defaults = core.defaults
        core.defaults = lambda: initial
        try:
            # Outer lock covers preflight + adapter + core; call undecorated core once.
            result = core.Component.install.__wrapped__(self, native=False)
        finally:
            core.defaults = original_defaults
        receipt = read_json(self.receipt)
        receipt.update(distribution=APP, shortcut=self.shortcut)
        write_json(self.receipt, receipt)
        self.bin.chmod(0o755)
        if native and result['files_changed']:
            subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
            subprocess.run(['systemctl', '--user', 'enable', '--now', 'nda-native-search.timer'], check=True)
            core.ipc('rescanPlugins')
            subprocess.run(['hyprctl', 'reload'], check=True, stdout=subprocess.DEVNULL)
            errors = subprocess.check_output(['hyprctl', 'configerrors'], text=True).strip()
            if errors:
                raise ValueError(errors)
        return result

    @core.serialized
    def configure(self, roots):
        current = load()
        current['roots'] = roots_config(roots)['roots']
        write_json(CONFIG, validate(current))
        return {'status': 'configured', 'note': 'Run refresh to build the selected roots'}


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description='Native Search standalone lifecycle')
    parser.add_argument('action', choices=['plan', 'install', 'update', 'remove', 'doctor', 'configure', 'status', 'refresh', 'show', 'hide', 'toggle', 'version'])
    parser.add_argument('--shortcut', help='Opt-in unused chord, e.g. SUPER+CTRL+F; none removes it')
    parser.add_argument('--root', action='append', help='Explicit indexed directory; repeat for multiple roots')
    args = parser.parse_args(argv)
    if args.shortcut is not None and args.action not in ('plan', 'install', 'update'):
        parser.error('--shortcut is only valid with plan/install/update')
    if args.root and args.action not in ('install', 'configure'):
        parser.error('--root is only valid with install/configure')
    component = Distribution(args.shortcut)
    if args.action == 'plan':
        result = component.preview()
    elif args.action in ('install', 'update'):
        result = component.install(roots=args.root)
    elif args.action == 'configure':
        if not args.root:
            parser.error('configure requires --root; it replaces the root list')
        result = component.configure(args.root)
    elif args.action in ('remove', 'doctor'):
        result = getattr(component, args.action)()
    elif args.action == 'version':
        result = {'version': read_json(core.SOURCE / 'manifest.json')['version']}
    else:
        return core.main([args.action])
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print('Native Search: ' + str(error), file=sys.stderr)
        raise SystemExit(2)
