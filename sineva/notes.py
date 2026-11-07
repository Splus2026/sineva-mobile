"""Конспект (цитаты из найденного) и дополнение книг своим текстом."""
import os
import re
import zipfile
from xml.sax.saxutils import escape

from .textio import TEXT_ENCODINGS

_XML_BAD_RE = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]')


def append_quote(path: str, text: str, source: str = '') -> None:
    """Формат десктопной Синевы+: «.......цитата» и строка «(источник)»."""
    text = text.strip()
    if not text:
        return
    entry = "......." + text + "\n" + (f"({source})\n" if source else '')
    prefix = ''
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, 'rb') as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b'\n':
                prefix = '\n'
    with open(path, 'ab') as f:
        f.write((prefix + entry).encode('utf-8'))


def _detect_encoding(raw: bytes) -> str:
    for enc in TEXT_ENCODINGS:
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return 'utf-8'


def append_to_book(path: str, text: str) -> None:
    """Дописывает text в конец файла-книги (.txt в его собственной кодировке или .docx)."""
    text = text.strip('\n')
    if not text.strip():
        raise ValueError("Пустой текст")
    ext = os.path.splitext(path)[1].lower()
    if ext == '.docx':
        _append_docx(path, text)
        return
    with open(path, 'rb') as f:
        raw = f.read()
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):          # UTF-16 из «Блокнота»: дописываем в той же кодировке
        enc = 'utf-16-le' if raw.startswith(b'\xff\xfe') else 'utf-16-be'
        tail = '\n'.encode(enc)
        data = text.encode(enc)
        with open(path, 'ab') as f:
            f.write((b'' if raw.endswith(tail) else tail) + data + tail)
        return
    if raw.startswith(b'\xef\xbb\xbf'):
        enc = 'utf-8'
    else:
        enc = _detect_encoding(raw)
    try:
        data = text.encode(enc)
    except UnicodeEncodeError:
        raise ValueError(f"В тексте есть символы, которых нет в кодировке файла ({enc})")
    sep = b'' if (not raw or raw.endswith(b'\n')) else b'\n'
    tmp = path + '.part'
    with open(tmp, 'wb') as f:
        f.write(raw + sep + data + b'\n')
    os.replace(tmp, path)


def _append_docx(path: str, text: str) -> None:
    paragraphs = ''.join(
        '<w:p><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>' % escape(_XML_BAD_RE.sub('', line))
        for line in text.split('\n'))
    tmp = path + '.part'
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zout:
        found = False
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == 'word/document.xml':
                xml = data.decode('utf-8')
                i = xml.rfind('<w:sectPr')
                if i == -1 or i < xml.rfind('</w:p>'):      # sectPr внутри абзаца — не то; вставляем перед </w:body>
                    i = xml.rfind('</w:body>')
                if i == -1:
                    raise ValueError("word/document.xml: не найден <w:body>")
                xml = xml[:i] + paragraphs + xml[i:]
                data = xml.encode('utf-8')
                found = True
            zout.writestr(item, data)
        if not found:
            raise ValueError("Не найден word/document.xml")
    os.replace(tmp, path)
