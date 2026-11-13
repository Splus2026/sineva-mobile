"""Строка настроек внутри самой книги: первая строка файла (для Word — первая строка текста) вида

    mconfig=" isida_1.docx = Разоблаченная Изида, N2000"

Имя файла в ней должно совпадать с фактическим именем книги; тогда название и разделитель берутся оттуда.
"""
import os
import re
from typing import Optional, Tuple

from .textio import _get_docx_paragraphs_cached, decode_text

_QUOTES = '"“”„«»\''
_LINE = re.compile(r'^\s*mconfig\s*=\s*[' + _QUOTES + r']\s*(.*?)\s*[' + _QUOTES + r']\s*$', re.IGNORECASE)


def first_line(path: str) -> str:
    """Первая строка книги (.txt — по первым байтам, .docx — первый абзац); пусто, если прочитать не удалось."""
    try:
        if os.path.splitext(path)[1].lower() == '.docx':
            paras = _get_docx_paragraphs_cached(path)
            return (paras[0] if paras else '').strip()
        with open(path, 'rb') as f:
            head = f.read(4096)
        return decode_text(head).lstrip('﻿').split('\n', 1)[0].strip()
    except Exception:
        return ''


def parse(line: str) -> Optional[Tuple[str, str, str]]:
    """(имя файла, название, разделитель) из строки mconfig или None, если строка не такого вида."""
    m = _LINE.match(line or '')
    if not m or '=' not in m.group(1):
        return None
    name, rest = m.group(1).split('=', 1)
    if ',' not in rest:
        return None
    display, sep = rest.rsplit(',', 1)
    name, display, sep = name.strip(), display.strip(), sep.strip()
    return (name, display, sep) if name and display and sep else None


def read(path: str) -> Tuple[Optional[Tuple[str, str]], str]:
    """((название, разделитель) или None, пояснение). Пояснение пусто, если mconfig в книге нет."""
    line = first_line(path)
    if 'mconfig' not in line.lower():
        return None, ''
    parsed = parse(line)
    if not parsed:
        return None, "строка mconfig в книге записана неверно (нужно: mconfig=\"файл.docx = Название, N2000\")"
    name, display, sep = parsed
    actual = os.path.basename(path)
    if name.casefold() != actual.casefold():
        return None, f"имя файла в mconfig ({name}) не совпадает с именем книги ({actual}) — строка пропущена"
    return (display, sep), ''


def strip(text: str) -> str:
    """Текст без служебной первой строки mconfig (в поиске и чтении её быть не должно)."""
    head, sep, rest = text.lstrip('﻿').partition('\n')
    return rest if _LINE.match(head.strip()) else text


def format_line(file_name: str, display: str, separator: str) -> str:
    clean = lambda t: t.replace('"', '').replace('\n', ' ').strip()
    return f'mconfig="{clean(file_name)} = {clean(display)}, {clean(separator)}"'


def write(path: str, display: str, separator: str) -> None:
    """Записывает (или заменяет) строку mconfig первой строкой книги: .txt — первой строкой файла,
       .docx — первым абзацем. Остальной текст не меняется."""
    line = format_line(os.path.basename(path), display, separator)
    if os.path.splitext(path)[1].lower() == '.docx':
        _write_docx(path, line)
    else:
        _write_txt(path, line)


def _atomic(path: str, data: bytes) -> None:
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def _write_txt(path: str, line: str) -> None:
    with open(path, 'rb') as f:
        raw = f.read()
    bom = raw.startswith(b'\xef\xbb\xbf')
    text = decode_text(raw)
    eol = '\r\n' if '\r\n' in text[:4096] else '\n'
    head, _sep, rest = text.partition('\n')
    body = rest if _LINE.match(head.strip()) else text          # старая строка mconfig заменяется
    out = (line + eol + body).encode('utf-8')
    _atomic(path, (b'\xef\xbb\xbf' if bom else b'') + out)


def _write_docx(path: str, line: str) -> None:
    import zipfile
    from xml.sax.saxutils import escape
    with zipfile.ZipFile(path) as z:
        xml = z.read('word/document.xml').decode('utf-8')
        infos = [(i, z.read(i.filename)) for i in z.infolist()]
    m_body = re.search(r'<w:body[^>]*>', xml)
    if not m_body:
        raise ValueError("в .docx не найден <w:body>")
    new_p = f'<w:p><w:r><w:t xml:space="preserve">{escape(line)}</w:t></w:r></w:p>'
    start = m_body.end()
    m_p = re.compile(r'<w:p\b[^>]*?/>|<w:p\b[^>]*>.*?</w:p>', re.S).match(xml, start)
    if m_p:
        text = ''.join(re.findall(r'<w:t[^>]*>([^<]*)</w:t>', m_p.group(0)))
        if _LINE.match(text.strip().replace('&quot;', '"')):      # прежняя строка mconfig — заменяется
            xml = xml[:start] + new_p + xml[m_p.end():]
        else:
            xml = xml[:start] + new_p + xml[start:]
    else:
        xml = xml[:start] + new_p + xml[start:]
    tmp = path + '.tmp'
    with zipfile.ZipFile(tmp, 'w') as out:
        for info, data in infos:
            out.writestr(info, xml.encode('utf-8') if info.filename == 'word/document.xml' else data,
                         compress_type=info.compress_type)
    os.replace(tmp, path)
