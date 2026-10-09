"""Real plocate fixtures plus action/protocol/lifecycle regression boundaries."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2] / 'components/native-search'
sys.path.insert(0, str(SOURCE))
import ns_config as config
import ns_index as index
import ns_search as search
import ns_actions as actions
import lifecycle


class NativeSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='native-search-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'root'
        self.root.mkdir()
        self.env = dict(os.environ, XDG_CONFIG_HOME=str(self.base / 'config'),
                        XDG_DATA_HOME=str(self.base / 'data'), XDG_STATE_HOME=str(self.base / 'state'))
        for module, name, value in [(config, 'CONFIG', self.base / 'config/nda-omarchy-stack/native-search/config.json'),
                (config, 'DATA', self.base / 'data/nda-omarchy-stack/native-search'),
                (config, 'STATE', self.base / 'state/nda-omarchy-stack/native-search')]:
            old = getattr(module, name)
            setattr(module, name, value)
            self.addCleanup(setattr, module, name, old)
        for module, name in [(index, 'DATA'), (search, 'DATA'), (lifecycle, 'CONFIG'), (lifecycle, 'STATE')]:
            old = getattr(module, name)
            setattr(module, name, getattr(config, name))
            self.addCleanup(setattr, module, name, old)
        env_patch = patch.dict(os.environ, self.env)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.cfg = {'version': 1, 'roots': [{'id': 'fixture', 'path': str(self.root), 'enabled': True,
            'mount': config.mount_identity(self.root)}], 'prunenames': ['node_modules', '.git'],
            'prunepaths': [], 'prunefs': [], 'refresh_seconds': 3600,
            'candidate_budget': 5000, 'result_budget': 100, 'timeout_seconds': 3,
            'kind': 'all', 'extension': ''}
        config.write_json(config.CONFIG, self.cfg)
        self.engine = search.Search()

    def build(self):
        result = index.refresh(self.cfg)
        self.assertEqual(result[0]['status'], 'updated', result)
        return result[0]

    def query(self, query, **kwargs):
        return self.engine.query(self.cfg, dict(query=query, **kwargs), threading.Event())

    def test_hostile_names_lossless_and_literal(self):
        names = [b'a[1]*?.txt', b'-leading.txt', b'quote\'";$(touch INJECTED).txt',
                 b'line\nbreak.txt', 'Unicode café.txt'.encode(), b'invalid-\xff.txt', b'back\\slash.txt']
        for name in names:
            fd = os.open(os.fsencode(self.root) + b'/' + name, os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(fd)
        self.build()
        for term, name in [('[1]*?', names[0]), ('-leading', names[1]), ('$(touch', names[2]),
                           ('line break', names[3]), ('CAFÉ', names[4]), ('invalid-', names[5]),
                           ('back\\slash', names[6])]:
            result = self.query(term)['results']
            self.assertEqual(len(result), 1, term)
            self.assertEqual(self.engine.identities[result[0]['id']][2], os.fsencode(self.root) + b'/' + name)
        self.assertFalse((self.root / 'INJECTED').exists())

    def test_filters_and_ranking(self):
        (self.root / 'report').mkdir()
        (self.root / 'report.pdf').touch()
        (self.root / 'other-report.md').touch()
        (self.root / 'report/final.md').touch()
        self.build()
        self.assertEqual(self.query('report')['results'][0]['name'], 'report')
        self.assertEqual(len(self.query('report', kind='folders')['results']), 1)
        self.assertEqual([r['name'] for r in self.query('report', extension='pdf')['results']], ['report.pdf'])
        self.assertTrue(all(r['kind'] == 'file' for r in self.query('report', kind='files')['results']))

    def test_empty_no_enumeration(self):
        with patch.object(search.subprocess, 'Popen', side_effect=AssertionError('Must not enumerate')):
            self.assertEqual(self.query('  ')['results'], [])

    def test_fuzzy_subsequence_typos_and_literal_mode(self):
        for name in ('quarterly-report.pdf', 'report.pdf', 'unrelated.txt'):
            (self.root / name).touch()
        self.build()
        self.assertEqual(self.query('qrtrly', mode='literal')['results'], [])
        self.assertEqual(self.query('qrtrly', mode='fuzzy')['results'][0]['name'], 'quarterly-report.pdf')
        self.assertIn('report.pdf', [r['name'] for r in self.query('repurt', mode='fuzzy')['results']])
        self.assertEqual(self.query('qrtrly repurt', mode='fuzzy', extension='pdf')['results'][0]['name'], 'quarterly-report.pdf')
        self.assertEqual(self.query('qrtrly missing', mode='fuzzy')['results'], [])

    def test_search_preferences_validate_and_migrate(self):
        validated = config.validate(self.cfg.copy())
        self.assertEqual(validated['search_mode'], 'literal')
        self.assertEqual(validated['fuzzy_tolerance'], 1)
        with self.assertRaises(ValueError):
            config.validate(dict(validated, fuzzy_tolerance=3))

    def test_candidate_cap_truthful(self):
        for n in range(110):
            (self.root / ('broad-%03d.txt' % n)).touch()
        self.cfg['candidate_budget'] = 100
        self.build()
        result = self.query('broad', limit=10)
        self.assertTrue(result['partial'])
        self.assertEqual(result['shown'], 10)
        self.assertNotIn('total', result)

    def test_root_allocation_fair(self):
        second = self.base / 'second'
        second.mkdir()
        self.cfg['roots'].append({'id': 'second', 'path': str(second), 'enabled': True, 'mount': config.mount_identity(second)})
        for n in range(80):
            (self.root / ('broad-%03d' % n)).touch()
            (second / ('broad-%03d' % n)).touch()
        self.cfg['candidate_budget'] = 100
        self.build()
        result = self.query('broad')
        self.assertEqual({r['root'] for r in result['results']}, {'fixture', 'second'})
        self.assertTrue(result['partial'])
        selected = self.query('broad', roots=['second'])
        self.assertEqual({r['root'] for r in selected['results']}, {'second'})
        with self.assertRaisesRegex(ValueError, 'Unknown root'):
            self.query('broad', roots=['foreign'])

    def test_offline_retains_generation(self):
        (self.root / 'known.txt').touch()
        previous = self.build()
        self.cfg['roots'][0]['mount']['uuid'] = 'wrong-identity'
        result = index.refresh(self.cfg)[0]
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['generation'], previous['generation'])
        self.assertTrue((config.DATA / previous['database']).exists())
        self.assertFalse(index.status(self.cfg)[0]['online'])
        self.assertEqual(self.query('known')['results'], [])

    def test_failed_build_retains_generation(self):
        (self.root / 'known').touch()
        previous = self.build()
        real_run = subprocess.run
        def fail_updatedb(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1) if 'updatedb' in cmd else real_run(cmd, **kwargs)
        with patch.object(index.subprocess, 'run', side_effect=fail_updatedb):
            result = index.refresh(self.cfg)[0]
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['last_success'], previous['last_success'])
        self.assertEqual(self.query('known')['shown'], 1)

    def test_disk_full_publication_preserves_snapshot(self):
        import errno
        (self.root / 'known').touch()
        previous = self.build()
        real_replace = os.replace
        def disk_full(source, target):
            if str(target).endswith('.db'):
                raise OSError(errno.ENOSPC, 'No space left on device')
            return real_replace(source, target)
        with patch.object(index.os, 'replace', side_effect=disk_full):
            result = index.refresh(self.cfg)[0]
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['generation'], previous['generation'])
        self.assertIn('No space', result['error'])
        self.assertEqual(self.query('known')['shown'], 1)

    def test_real_killed_refresh_preserves_snapshot(self):
        import signal
        import time
        (self.root / 'known').touch()
        previous = self.build()
        fakebin = self.base / 'bin'
        fakebin.mkdir()
        updatedb = fakebin / 'updatedb'
        updatedb.write_text('#!/bin/sh\nexec /usr/bin/sleep 60\n')
        updatedb.chmod(0o700)
        env = dict(self.env, PATH=str(fakebin) + ':' + self.env['PATH'])
        proc = subprocess.Popen(['/usr/bin/python3', '-B', str(SOURCE / 'helper.py'), 'refresh'],
                                env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            found = False
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                children = subprocess.run(['pgrep', '-P', str(proc.pid)], stdout=subprocess.PIPE,
                                          stderr=subprocess.DEVNULL).stdout.split()
                for child in children:
                    try:
                        if b'/usr/bin/sleep\x0060' in Path('/proc/' + child.decode() + '/cmdline').read_bytes():
                            found = True
                    except OSError:
                        pass
                if found:
                    break
                time.sleep(.01)
            self.assertTrue(found, 'Fixture updatedb child did not start')
        finally:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=3)
        state = index.status(self.cfg)[0]
        self.assertIn('Interrupted', state['error'])
        self.assertEqual(state['generation'], previous['generation'])
        self.assertEqual(state['last_success'], previous['last_success'])
        self.assertEqual(self.query('known')['shown'], 1)

    def test_permission_denied_root_preserves_snapshot(self):
        (self.root / 'known').touch()
        previous = self.build()
        self.root.chmod(0)
        try:
            result = index.refresh(self.cfg)[0]
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['generation'], previous['generation'])
            self.assertTrue(self.query('known')['errors'])
        finally:
            self.root.chmod(0o700)

    def test_missing_query_executable_is_not_no_matches(self):
        (self.root / 'known').touch()
        self.build()
        with patch.object(search, 'available', return_value=True), patch.object(search.subprocess, 'Popen',
                side_effect=FileNotFoundError('plocate executable missing')):
            with self.assertRaisesRegex(FileNotFoundError, 'plocate executable missing'):
                self.query('known')

    def test_concurrent_refresh_lock(self):
        import fcntl
        config.private_dir(config.DATA)
        with (config.DATA / 'fixture.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertEqual(index.refresh(self.cfg)[0]['status'], 'already-building')
            self.assertTrue(index.status(self.cfg)[0]['building'])

    def test_battery_deferral_and_manual_override(self):
        (self.root / 'known').touch()
        self.build()
        info = index.metadata(self.cfg['roots'][0])
        info['last_success'] -= 7201
        config.write_json(config.DATA / 'fixture.json', info)
        with patch.object(index, 'on_battery', return_value=True):
            self.assertEqual(index.refresh(self.cfg, scheduled=True)[0]['status'], 'deferred-battery')
            self.assertEqual(index.refresh(self.cfg)[0]['status'], 'updated')

    def test_first_build_requires_explicit_refresh(self):
        self.assertEqual(index.refresh(self.cfg, scheduled=True)[0]['status'], 'needs-first-manual-build')

    def test_disconnect_before_publication_retains_snapshot(self):
        (self.root / 'known').touch()
        previous = self.build()
        with patch.object(index, 'available', side_effect=[True, False]):
            result = index.refresh(self.cfg)[0]
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['generation'], previous['generation'])
        self.assertEqual(result['database'], previous['database'])

    def test_interrupted_attempt_status_is_not_fresh_success(self):
        (self.root / 'known').touch()
        previous = self.build()
        info = index.metadata(self.cfg['roots'][0])
        info['last_attempt'] = info['last_success'] + 1
        config.write_json(config.DATA / 'fixture.json', info)
        state = index.status(self.cfg)[0]
        self.assertIn('Interrupted', state['error'])
        self.assertEqual(state['generation'], previous['generation'])

    def test_generation_switch_expires_actions(self):
        (self.root / 'known').touch()
        self.build()
        row = self.query('known')['results'][0]
        self.build()
        with self.assertRaisesRegex(ValueError, 'generation changed'):
            actions.action(self.engine, self.cfg, {'id': row['id'], 'action': 'open'})

    def test_deleted_activation(self):
        path = self.root / 'known'
        path.touch()
        self.build()
        row = self.query('known')['results'][0]
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'deleted'):
            actions.action(self.engine, self.cfg, {'id': row['id'], 'action': 'open'})

    def test_action_argv_and_uri(self):
        path = self.root / "quote ' ;$?.txt"
        path.touch()
        self.build()
        row = self.query('quote')['results'][0]
        with patch.object(actions, 'available', return_value=True), patch.object(actions, 'mount_identity', return_value=self.cfg['roots'][0]['mount']), patch.object(actions.subprocess, 'run') as run:
            actions.action(self.engine, self.cfg, {'id': row['id'], 'action': 'copy-uri'})
            self.assertEqual(run.call_args.kwargs['input'], actions.uri(os.fsencode(path)).encode())
            self.assertNotIn('shell', run.call_args.kwargs)
        with patch.object(actions, 'available', return_value=True), patch.object(actions, 'mount_identity', return_value=self.cfg['roots'][0]['mount']), patch.object(actions.subprocess, 'Popen') as popen:
            actions.action(self.engine, self.cfg, {'id': row['id'], 'action': 'open'})
            self.assertEqual(popen.call_args.args[0], ['xdg-open', actions.uri(os.fsencode(path))])
        self.assertIn('%27', actions.uri(os.fsencode(path)))

    def test_reveal_honest_fallback(self):
        (self.root / 'known').touch()
        self.build()
        row = self.query('known')['results'][0]
        with patch.object(actions, 'available', return_value=True), patch.object(actions, 'mount_identity', return_value=self.cfg['roots'][0]['mount']), patch.object(actions.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)), patch.object(actions.subprocess, 'Popen'):
            result = actions.action(self.engine, self.cfg, {'id': row['id'], 'action': 'reveal'})
        self.assertIn('selection unavailable', result['message'])

    def test_cancellation(self):
        (self.root / 'known').touch()
        self.build()
        token = threading.Event()
        token.set()
        with self.assertRaises(InterruptedError):
            self.engine.query(self.cfg, {'query': 'known'}, token)

    def test_protocol_newest_and_malformed(self):
        (self.root / 'known').touch()
        self.build()
        proc = subprocess.Popen([sys.executable, str(SOURCE / 'helper.py'), 'serve'], env=self.env,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        requests = [dict(version=1, request_id=i, operation='search', arguments={'query': 'known' if i == 20 else 'broad'}) for i in range(21)]
        raw = b''.join(json.dumps(r).encode() + b'\n' for r in requests)
        # Allow final result to complete before EOF cancels outstanding work.
        proc.stdin.write(raw)
        proc.stdin.flush()
        import select
        self.assertTrue(select.select([proc.stdout], [], [], 5)[0])
        lines = []
        while True:
            event = json.loads(proc.stdout.readline())
            lines.append(event)
            if event['request_id'] == 20:
                break
        self.assertEqual(lines[-1]['shown'], 1)
        out, err = proc.communicate(b'{"version": 2}\n', timeout=5)
        self.assertIn(b'Unsupported protocol', out)
        self.assertEqual(err, b'')
        self.assertEqual(proc.returncode, 0)

    def test_permissions_and_exclusions(self):
        previous_umask = os.umask(0o022)
        self.addCleanup(os.umask, previous_umask)
        (self.root / 'node_modules').mkdir()
        (self.root / 'node_modules/omitted').touch()
        (self.root / '.authored-hidden').touch()
        self.build()
        self.assertEqual(self.query('omitted')['shown'], 0)
        self.assertEqual(self.query('authored-hidden')['shown'], 1)
        self.assertEqual(config.DATA.stat().st_mode & 0o777, 0o700)
        self.assertTrue(all(p.stat().st_mode & 0o077 == 0 for p in config.DATA.iterdir()))

    def test_schema_bounds_and_overlaps(self):
        self.cfg['roots'][0]['path'] = str(self.root / '..' / 'root')
        config.validate(self.cfg)
        self.assertEqual(self.cfg['roots'][0]['path'], str(self.root))
        for key, value in [('version', 2), ('candidate_budget', 99999), ('result_budget', 0)]:
            with self.assertRaises(ValueError):
                config.validate(dict(self.cfg, **{key: value}))
        duplicate = dict(self.cfg, roots=self.cfg['roots'] + [dict(self.cfg['roots'][0], id='other')])
        with self.assertRaisesRegex(ValueError, 'overlapping'):
            config.validate(duplicate)

    def test_changed_root_scope_requires_new_index(self):
        (self.root / 'known').touch()
        previous = self.build()
        replacement = self.base / 'replacement'
        replacement.mkdir()
        self.cfg['roots'][0]['path'] = str(replacement)
        state = index.status(self.cfg)[0]
        self.assertTrue(state['scope_changed'])
        self.assertTrue(state['stale'])
        self.assertFalse(state['indexed'])
        result = self.query('known')
        self.assertIn('scope/exclusions changed', result['errors'][0])
        self.assertTrue((config.DATA / previous['database']).exists())

    def test_changed_exclusions_expire_previous_actions(self):
        folder = self.root / 'secret'
        folder.mkdir()
        (folder / 'known').touch()
        self.build()
        row = self.query('known')['results'][0]
        self.cfg['prunenames'].append('secret')
        self.assertFalse(index.status(self.cfg)[0]['indexed'])
        self.assertIn('scope/exclusions changed', self.query('known')['errors'][0])
        # Retain the old opaque map to prove the action boundary independently.
        engine = search.Search()
        engine.identities[row['id']] = (self.cfg['roots'][0].copy(), index.metadata(self.cfg['roots'][0])['generation'], os.fsencode(folder / 'known'))
        with self.assertRaisesRegex(ValueError, 'scope changed'):
            actions.action(engine, self.cfg, {'id': row['id'], 'action': 'open'})

    def test_replaced_parent_symlink_cannot_escape_root(self):
        folder = self.root / 'folder'
        folder.mkdir()
        (folder / 'known').touch()
        self.build()
        row = self.query('known')['results'][0]
        outside = self.base / 'outside'
        folder.rename(outside)
        folder.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'parent moved outside'):
            actions.action(self.engine, self.cfg, {'id': row['id'], 'action': 'open'})

    def test_unexpected_submount_cannot_activate_old_path(self):
        (self.root / 'known').touch()
        self.build()
        row = self.query('known')['results'][0]
        changed = dict(self.cfg['roots'][0]['mount'], target=str(self.root / 'submount'))
        with patch.object(actions, 'available', return_value=True), patch.object(actions, 'mount_identity', return_value=changed):
            with self.assertRaisesRegex(ValueError, 'unexpected subordinate mount'):
                actions.action(self.engine, self.cfg, {'id': row['id'], 'action': 'open'})

    def test_filesystem_root_is_not_its_own_submount(self):
        data = json.dumps({'filesystems': [{'target': '/'}, {'target': '/home'}]}).encode()
        with patch.object(index.subprocess, 'check_output', return_value=data):
            self.assertEqual(index.submounts({'path': '/'}), ['/home'])

    def test_empty_and_metacharacter_root_validates(self):
        renamed = self.base / 'root [1]*?'
        self.root.rename(renamed)
        self.root = renamed
        self.cfg['roots'][0]['path'] = str(renamed)
        info = self.build()
        self.assertEqual(info['indexed_paths'], 0)
        self.assertTrue(index.status(self.cfg)[0]['indexed'])
        (renamed / 'known').touch()
        self.build()
        self.assertEqual(self.query('known')['shown'], 1)

    def test_metadata_publication_failure_retains_last_good(self):
        (self.root / 'known').touch()
        previous = self.build()
        def fail_new_pointer(path, value):
            if value.get('generation') != previous['generation']:
                raise OSError('Metadata publication failed')
            config.write_json(path, value)
        with patch.object(index, 'write_json', side_effect=fail_new_pointer):
            result = index.refresh(self.cfg)[0]
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['generation'], previous['generation'])
        self.assertEqual(result['last_success'], previous['last_success'])
        self.assertEqual(self.query('known')['shown'], 1)

    def test_malformed_extension_is_a_protocol_error(self):
        with self.assertRaisesRegex(ValueError, 'Extension must be text'):
            self.query('known', extension=42)

    def test_exclusion_path_with_spaces(self):
        excluded = self.root / 'excluded directory'
        excluded.mkdir()
        (excluded / 'omitted').touch()
        (self.root / 'included').touch()
        self.cfg['prunepaths'] = [str(excluded)]
        config.validate(self.cfg)
        self.build()
        self.assertEqual(self.query('omitted')['shown'], 0)
        self.assertEqual(self.query('included')['shown'], 1)

    def test_missing_index_is_not_silent_no_matches(self):
        result = self.query('known')
        self.assertEqual(result['shown'], 0)
        self.assertIn('build index first', result['errors'][0])

    def setup_installer(self):
        deps = patch.object(lifecycle, 'DEPENDENCIES', [])
        deps.start()
        self.addCleanup(deps.stop)
        component = lifecycle.Component()
        component.shell.parent.mkdir(parents=True, exist_ok=True)
        component.shell.write_text('{"version":1,"plugins":[],"unrelated":true}\n')
        component.bindings.parent.mkdir(parents=True, exist_ok=True)
        component.bindings.write_text('-- user bindings\n')
        return component

    def test_install_twice_remove_preserves_later_edits(self):
        component = self.setup_installer()
        first = component.install(native=False)
        self.assertGreater(first['files_changed'], 0)
        self.assertEqual(component.install(native=False)['files_changed'], 0)
        shell = config.read_json(component.shell)
        shell['later'] = 42
        config.write_json(component.shell, shell)
        component.bindings.write_text(component.bindings.read_text() + '-- later user edit\n')
        self.assertEqual(component.remove(native=False)['status'], 'removed')
        self.assertEqual(config.read_json(component.shell)['later'], 42)
        self.assertIn('-- later user edit', component.bindings.read_text())
        self.assertTrue(config.CONFIG.exists())
        self.assertGreater(component.install(native=False)['files_changed'], 0)

    def test_remove_refuses_managed_edit(self):
        component = self.setup_installer()
        component.install(native=False)
        qml = next(component.plugin.glob('ui*/Search.qml'))
        qml.write_text(qml.read_text() + '// user customization\n')
        with self.assertRaisesRegex(ValueError, 'Managed payload changed'):
            component.remove(native=False)
        self.assertTrue(qml.exists())

    def test_corrupt_database_is_error(self):
        (self.root / 'known').touch()
        info = self.build()
        (config.DATA / info['database']).write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'plocate failed'):
            self.query('known')


if __name__ == '__main__':
    unittest.main()
