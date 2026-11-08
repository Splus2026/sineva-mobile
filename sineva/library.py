"""Библиотека: книги в папке books, их настройки (mconfig.ini) и индексы."""
import hashlib
import os
import threading
from dataclasses import dataclass
from typing import Callable, List, Optional

from .config import AppConfig
from .index import INDEX_FORMAT, build_index, read_info
from .paths import AppPaths

BOOK_EXTENSIONS = ('.txt', '.docx')
# служебные файлы, которые лежат рядом с книгами, но книгами не являются
SERVICE_FILES = {'readme.txt', 'requirements.txt', 'requirements-compact.txt', '0_конспект.txt'}

_TRANSLIT = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y',
    'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f',
    'х': 'h', 'ц': 'c', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
}

NOT_INDEXED, READY, STALE = 'not_indexed', 'ready', 'stale'


def safe_index_name(file_name: str) -> str:
    """Имя файла индекса: транслит + короткий хеш (разные книги не затирают друг друга, кириллица не мешает)."""
    stem = os.path.splitext(file_name)[0].lower()
    ascii_stem = ''.join(_TRANSLIT.get(ch, ch) for ch in stem)
    ascii_stem = ''.join(ch if ch.isalnum() and ch.isascii() else '_' for ch in ascii_stem).strip('_')[:40] or 'book'
    return f"{ascii_stem}_{hashlib.md5(file_name.encode('utf-8')).hexdigest()[:6]}.db"


@dataclass
class Book:
    file_name: str          # имя файла: ключ в [Bases]
    path: str
    display: str            # имя для списков
    separator: str
    index_path: str
    state: str = NOT_INDEXED
    topics: int = 0
    size: int = 0

    @property
    def ready(self) -> bool:
        return self.state in (READY, STALE) and os.path.exists(self.index_path)


class Library:
    def __init__(self, paths: AppPaths, config: AppConfig):
        self.paths = paths
        self.config = config
        self.books: List[Book] = []
        self._lock = threading.Lock()
        self.scan()

    # ---------- состав ----------
    def scan(self) -> List[Book]:
        """Перечитывает конфигурацию и папку книг — то, что кнопка обновления делает в главном окне ПК-версии."""
        self.config.load()
        books = []
        try:
            names = sorted(os.listdir(self.paths.books_dir), key=str.lower)
        except OSError:
            names = []
        for name in names:
            path = os.path.join(self.paths.books_dir, name)
            low = name.lower()
            if not os.path.isfile(path) or not low.endswith(BOOK_EXTENSIONS) or low in SERVICE_FILES or name.startswith('~$'):
                continue
            display, sep, _excl = self.config.base_for(name)
            book = Book(name, path, display, sep, os.path.join(self.paths.index_dir, safe_index_name(name)),
                        size=os.path.getsize(path))
            self._refresh_state(book)
            books.append(book)
        self.books = books
        return books

    def _refresh_state(self, book: Book) -> None:
        info = read_info(book.index_path)
        if not info or 'topics' not in info:
            book.state, book.topics = NOT_INDEXED, 0
            return
        book.topics = int(info.get('topics', 0) or 0)
        try:
            st = os.stat(book.path)
            same = (info.get('source_size') == str(st.st_size) and info.get('source_mtime') == str(int(st.st_mtime))
                    and info.get('separator') == book.separator and info.get('format') == INDEX_FORMAT)
        except OSError:
            same = False
        book.state = READY if same else STALE

    def find(self, file_name: str) -> Optional[Book]:
        for b in self.books:
            if b.file_name == file_name:
                return b
        return None

    # ---------- индексация ----------
    def index_book(self, book: Book, progress: Optional[Callable[[int, int], None]] = None) -> int:
        with self._lock:
            n = build_index(book.path, book.index_path, book.separator, progress)
        self._refresh_state(book)
        return n

    def delete_index(self, book: Book) -> None:
        for p in (book.index_path, book.index_path + '.building'):
            if os.path.exists(p):
                os.remove(p)
        self._refresh_state(book)

    # ---------- книги ----------
    def add_book_file(self, src_path: str, name: Optional[str] = None) -> Book:
        """Копирует книгу в папку books (например, выбранную через системный диалог)."""
        import shutil
        name = name or os.path.basename(src_path)
        dst = os.path.join(self.paths.books_dir, name)
        if os.path.abspath(src_path) != os.path.abspath(dst):
            shutil.copy2(src_path, dst)
        self.scan()
        return self.find(name)

    def remove_book(self, book: Book, delete_file: bool = False) -> None:
        self.delete_index(book)
        if delete_file and os.path.exists(book.path):
            os.remove(book.path)
        self.config.remove_base(book.file_name)
        self.config.save()
        self.scan()

    def update_book_settings(self, book: Book, display: str, separator: str) -> None:
        _d, _s, excl = self.config.base_for(book.file_name)
        self.config.set_base(book.file_name, display or book.display, separator or book.separator, excl)
        self.config.save()
        self.scan()
