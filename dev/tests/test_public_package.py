"""Isolated standalone first install/update/remove; does not modify the desktop."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2] / 'components/native-search'


class PublicPackageTests(unittest.TestCase):
    def test_isolated_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config = home / 'config'
            (config / 'omarchy').mkdir(parents=True)
            (config / 'hypr').mkdir()
            (config / 'omarchy/shell.json').write_text('{"version":1,"plugins":[],"unrelated":true}')
            bindings = config / 'hypr/bindings.lua'
            bindings.write_text('-- user binding\n')
            roots = home / 'documents'
            roots.mkdir()
            env = dict(os.environ, HOME=tmp, XDG_CONFIG_HOME=str(config), XDG_DATA_HOME=str(home / 'data'),
                       XDG_STATE_HOME=str(home / 'state'), XDG_BIN_HOME=str(home / 'bin'),
                       PYTHONPATH=str(SOURCE), PYTHONDONTWRITEBYTECODE='1')
            script = r'''
from unittest.mock import patch
import public_cli as public
from ns_config import CONFIG, load
from pathlib import Path
import os
with patch('lifecycle.shutil.which', return_value='/fixture/tool'):
    item = public.Distribution()
    first = item.install(native=False, roots=[os.environ['HOME'] + '/documents'])
    assert first['files_changed'] > 0
    assert load()['roots'][0]['path'].endswith('/documents')
    assert len(load()['roots']) == 1
    assert public.Distribution().install(native=False)['files_changed'] == 0
    assert item.bin.is_file() and os.access(item.bin, os.X_OK)
    assert (item.plugin / 'public_cli.py').is_file()
    with item.bindings.open('a') as stream: stream.write('-- later user edit\n')
    upgraded = public.Distribution('SUPER+CTRL+F')
    upgraded.install(native=False)
    assert 'SUPER + CTRL + F' in upgraded.bindings.read_text()
    assert public.Distribution().shortcut == 'SUPER + CTRL + F'
    public.Distribution('none').install(native=False)
    assert 'SUPER + CTRL + F' not in upgraded.bindings.read_text()
    result = public.Distribution().remove(native=False)
    assert result['status'] == 'removed'
    assert CONFIG.exists()
    assert '-- later user edit' in upgraded.bindings.read_text()
    assert 'omarchy-native-search' not in upgraded.bindings.read_text()
    assert not item.bin.exists()
'''
            result = subprocess.run([sys.executable, '-B', '-c', script], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_no_implicit_binding_or_storage(self):
        result = subprocess.run([sys.executable, str(SOURCE / 'public_cli.py'), 'plan'],
                                env=dict(os.environ, XDG_STATE_HOME=tempfile.gettempdir() + '/native-search-empty-state'),
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(json.loads(result.stdout)['shortcut'])


if __name__ == '__main__':
    unittest.main()
