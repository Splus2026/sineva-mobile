"""Чтение книг: .txt (несколько кодировок) и .docx (прямой разбор XML, без python-docx).
Взято из Синевы+ (utils/text_processing.py) почти без изменений."""
import os
import zipfile
from collections import OrderedDict
from xml.etree import ElementTree as ET

# Кодировки, которые пробуются по очереди при чтении .txt файлов —
# используется всеми модулями, читающими файлы-источники (общий, единый список).
TEXT_ENCODINGS = ['utf-8', 'cp1251', 'latin-1', 'koi8-r']

_DOCX_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# Кеш уже извлечённых абзацев .docx — файл + время изменения + размер → список
# абзацев (без применения keep_empty_docx_paragraphs, он применяется после).
# Разбор .docx — самая медленная часть открытия файла по гиперссылке, а один
# и тот же файл-источник часто открывают повторно (разные топики из одного
# файла), поэтому повторные открытия того же файла не читают и не парсят его
# заново, пока он не изменился на диске. Размер кеша ограничен, чтобы не
# накапливать в памяти неограниченное количество больших документов.
_docx_paragraphs_cache = OrderedDict()
_DOCX_CACHE_MAX_FILES = 5


def _read_docx_paragraphs_fast(file_path):
    """Извлекает текст абзацев .docx прямым разбором word/document.xml, минуя
       тяжёлую объектную модель python-docx. На больших документах (десятки
       тысяч абзацев) это в 4-5 раз быстрее при полностью идентичном
       результате — проверено на документах с разрывами строк (<w:br/>),
       табуляцией (<w:tab/>), несколькими <w:r> в одном абзаце и пустыми
       абзацами. Как и doc.paragraphs в python-docx, берёт только абзацы,
       являющиеся непосредственными детьми <w:body> — абзацы внутри таблиц
       (<w:tbl>) не включаются."""
    with zipfile.ZipFile(file_path) as z:
        xml_bytes = z.read('word/document.xml')
    root = ET.fromstring(xml_bytes)
    body = root.find(f"{_DOCX_W_NS}body")
    if body is None:
        raise ValueError("word/document.xml: не найден <w:body>")
    paragraphs = []
    for p in body.findall(f"{_DOCX_W_NS}p"):
        parts = []
        for node in p.iter():
            tag = node.tag
            if tag == f"{_DOCX_W_NS}t":
                parts.append(node.text or "")
            elif tag == f"{_DOCX_W_NS}tab":
                parts.append("\t")
            elif tag in (f"{_DOCX_W_NS}br", f"{_DOCX_W_NS}cr"):
                parts.append("\n")
        paragraphs.append("".join(parts))
    return paragraphs


def _read_docx_paragraphs_with_docx_lib(file_path):
    """Надёжный, но медленный резервный способ через python-docx — на случай,
       если быстрый разбор XML не справился с каким-то нестандартным .docx."""
    try:
        from docx import Document
    except ImportError:
        raise Exception("Для чтения .docx установите python-docx")
    try:
        doc = Document(file_path)
        return [p.text for p in doc.paragraphs]
    except Exception as e:
        raise Exception(f"Ошибка чтения .docx: {e}")


def _get_docx_paragraphs_cached(file_path):
    try:
        stat = os.stat(file_path)
        cache_key = os.path.abspath(file_path)
    except OSError:
        stat = None
        cache_key = None

    if stat is not None and cache_key in _docx_paragraphs_cache:
        cached_mtime, cached_size, cached_paragraphs = _docx_paragraphs_cache[cache_key]
        if cached_mtime == stat.st_mtime and cached_size == stat.st_size:
            _docx_paragraphs_cache.move_to_end(cache_key)
            return cached_paragraphs

    try:
        paragraphs = _read_docx_paragraphs_fast(file_path)
    except Exception:
        # Нестандартная структура .docx; python-docx в APK не входит — пробуем, если он есть.
        paragraphs = _read_docx_paragraphs_with_docx_lib(file_path)

    if stat is not None:
        _docx_paragraphs_cache[cache_key] = (stat.st_mtime, stat.st_size, paragraphs)
        _docx_paragraphs_cache.move_to_end(cache_key)
        while len(_docx_paragraphs_cache) > _DOCX_CACHE_MAX_FILES:
            _docx_paragraphs_cache.popitem(last=False)

    return paragraphs


def read_text_file(file_path: str, keep_empty_docx_paragraphs: bool = True) -> str:
    """Читает содержимое файла-источника (.txt в одной из TEXT_ENCODINGS,
       либо .docx) и возвращает текст.

       Раньше эта логика была продублирована почти буквально в
       services/file_manager.py и services/search_controller.py — вынесена
       сюда как единая точка чтения файлов-источников.

       keep_empty_docx_paragraphs: у двух прежних копий этой логики было разное
       поведение для .docx — одна сохраняла пустые абзацы (пустые строки),
       другая их пропускала. Чтобы не изменить поведение ни в одном из мест
       (индексация файла и просмотр файла по гиперссылке), поведение
       выбирается этим параметром, а не унифицируется молча."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == '.docx':
        paragraphs = _get_docx_paragraphs_cached(file_path)
        if not keep_empty_docx_paragraphs:
            paragraphs = [p for p in paragraphs if p.strip()]
        return "\n".join(paragraphs)

    for enc in TEXT_ENCODINGS:
        try:
            with open(file_path, 'r', encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    raise Exception("Не удалось прочитать файл ни в одной из кодировок.")
