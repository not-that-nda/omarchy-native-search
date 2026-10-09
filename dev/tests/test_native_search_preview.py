"""Real bounded preview format decoding and cancellation; no private documents."""
import base64
import importlib.util
import pathlib
import sys
import tempfile
import threading
import unittest
import zipfile
from unittest.mock import patch

SOURCE = pathlib.Path(__file__).resolve().parents[2] / 'components/native-search'
sys.path.insert(0, str(SOURCE))
from ns_preview import Preview


class PreviewTests(unittest.TestCase):
    def test_text_bounded_and_binary_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 'note.txt'
            path.write_text('x' * 100000)
            self.assertEqual(len(Preview().file(path, '.txt')['text']), 16000)
            path.write_bytes(b'hello\0binary')
            self.assertEqual(Preview().file(path, '.txt')['kind'], 'none')

    def test_docx_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 'sample.docx'
            with zipfile.ZipFile(path, 'w') as doc:
                doc.writestr('word/document.xml', '<document><p>Hello document</p><p>Second paragraph</p></document>')
            self.assertEqual(Preview().file(path, '.docx')['text'], 'Hello document\n\nSecond paragraph')

    def test_image_and_pdf_render_to_png(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            for ext in ('.png', '.pdf'):
                path = pathlib.Path(tmp) / ('sample' + ext)
                Image.new('RGB', (100, 120), 'white').save(path)
                result = Preview().file(path, ext)
                self.assertEqual(result['kind'], 'image')
                self.assertTrue(base64.b64decode(result['source'].split(',', 1)[1]).startswith(b'\x89PNG'))

    def test_cancelled_selection_does_not_read(self):
        token = threading.Event()
        token.set()
        with patch('ns_preview.selected_path') as lookup:
            with self.assertRaises(InterruptedError):
                Preview().render(None, {}, {}, token)
            lookup.assert_not_called()


if __name__ == '__main__':
    unittest.main()
