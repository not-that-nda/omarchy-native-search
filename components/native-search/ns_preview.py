"""Bounded, local document previews. No office macros, network or persistent cache."""
import base64
import io
import os
import subprocess
import threading
import zipfile
from xml.etree import ElementTree
from ns_actions import selected_path

TEXT = {'.txt', '.md', '.json', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.log',
        '.csv', '.py', '.js', '.ts', '.qml', '.lua', '.sh', '.xml', '.html', '.css'}
IMAGES = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tif', '.tiff'}
LIMIT = 16000


class Preview:
    def __init__(self):
        self.lock = threading.Lock()

    def render(self, search, config, args, cancel):
        with self.lock:
            if cancel.is_set():
                raise InterruptedError()
            path = selected_path(search, config, args)
            if not os.path.isfile(path):
                return {'kind': 'none'}
            ext = os.path.splitext(os.fsdecode(path))[1].lower()
            try:
                result = self.file(path, ext)
            except (zipfile.BadZipFile, ElementTree.ParseError) as error:
                raise ValueError('Cannot read document preview') from error
            if cancel.is_set():
                raise InterruptedError()
            return result

    def file(self, path, ext):
        if ext not in TEXT | IMAGES | {'.pdf', '.docx', '.odt'}:
            return {'kind': 'none'}
        if os.path.getsize(path) > 64 * 1024 * 1024:
            return {'kind': 'text', 'text': 'Preview unavailable: file exceeds 64 MiB.', 'label': 'Preview limit'}
        if ext in TEXT:
            with open(path, 'rb') as stream:
                raw = stream.read(65536)
            if b'\0' in raw:
                return {'kind': 'none'}
            return {'kind': 'text', 'text': raw.decode('utf-8', errors='replace')[:LIMIT], 'label': 'Text · beginning of file'}
        if ext in {'.docx', '.odt'}:
            with zipfile.ZipFile(path) as archive:
                name = 'word/document.xml' if ext == '.docx' else 'content.xml'
                info = archive.getinfo(name)
                if info.file_size > 4 * 1024 * 1024:
                    raise ValueError('Document text exceeds preview limit')
                xml = archive.read(name)
                if b'<!DOCTYPE' in xml or b'<!ENTITY' in xml:
                    raise ValueError('Unsupported document XML')
                tree = ElementTree.fromstring(xml)
                paragraphs = [''.join(node.itertext()) for node in tree.iter()
                              if node.tag.rsplit('}', 1)[-1] in ('p', 'h')]
            return {'kind': 'text', 'text': '\n\n'.join(paragraphs)[:LIMIT], 'label': 'Document text · formatting simplified'}
        if ext == '.pdf':
            data = subprocess.run(['pdftoppm', '-f', '1', '-singlefile', '-scale-to', '1200',
                                   '-png', os.fsdecode(path)], check=True, timeout=5,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
            label = 'PDF · page 1'
        else:
            try:
                from PIL import Image, ImageOps
            except ImportError as error:
                raise ValueError('Image previews require the optional python-pillow package') from error
            with Image.open(path) as original:
                if original.width * original.height > 40_000_000:
                    raise ValueError('Image exceeds preview pixel limit')
                original.thumbnail((1200, 1200))
                image = ImageOps.exif_transpose(original).convert('RGBA')
                output = io.BytesIO()
                image.save(output, format='PNG')
                data = output.getvalue()
            label = 'Image'
        if len(data) > 6 * 1024 * 1024:
            raise ValueError('Rendered preview exceeds size limit')
        return {'kind': 'image', 'source': 'data:image/png;base64,' + base64.b64encode(data).decode(), 'label': label}
