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
