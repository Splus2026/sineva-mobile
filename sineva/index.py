"""Индекс одной книги — файл SQLite.

Таблицы как у FTS5-движка Синевы+ (docs — FTS5 с триграммами, doc_metadata — файл и позиция), плюс info —
сведения для проверки актуальности индекса. Если в SQLite телефона нет FTS5/trigram, docs становится
обычной таблицей, а поиск идёт перебором в Python (медленнее, но работает на книгах в единицы МБ).
"""
import os
import re
import sqlite3
import time
from typing import Callable, Dict, List, Optional, Tuple

from .queryparser import compute_matches, get_fragments_with_levels
from .splitter import split_into_sections_by_separator
from .textio import read_text_file

FTS_TABLE = "docs"


def fts5_trigram_available() -> bool:
    """Есть ли в этой сборке SQLite FTS5 с токенайзером trigram (нужен SQLite 3.34+)."""
    try:
        c = sqlite3.connect(':memory:')
        c.execute("CREATE VIRTUAL TABLE t USING fts5(content, tokenize='trigram')")
        c.close()
        return True
    except sqlite3.Error:
        return False


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def build_index(source_path: str, index_path: str, separator: str,
                progress: Optional[Callable[[int, int], None]] = None,
                use_fts: Optional[bool] = None) -> int:
    """Читает книгу, режет на топики и записывает индекс. Пишет во временный файл и заменяет им старый —
       прерванная индексация не оставляет испорченного индекса. Возвращает число топиков."""
    text = read_text_file(source_path)
    lines = text.splitlines(keepends=False)
    sections = split_into_sections_by_separator(lines, separator)
    if use_fts is None:
        use_fts = fts5_trigram_available()

    tmp = index_path + '.building'
    if os.path.exists(tmp):
        os.remove(tmp)
    conn = _connect(tmp)
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode = OFF")
        cur.execute("PRAGMA synchronous = OFF")
        if use_fts:
            cur.execute(f"CREATE VIRTUAL TABLE {FTS_TABLE} USING fts5(content, tokenize='trigram')")
        else:
            cur.execute(f"CREATE TABLE {FTS_TABLE}(content TEXT)")
        cur.execute("CREATE TABLE doc_metadata(rowid INTEGER PRIMARY KEY, file_name TEXT, position TEXT)")
        cur.execute("CREATE TABLE info(key TEXT PRIMARY KEY, value TEXT)")
        file_name = os.path.basename(source_path)
        total = len(sections)
        cur.execute("BEGIN")
        for i, sec in enumerate(sections, 1):
            content = '\n'.join(sec['lines'])
            cur.execute(f"INSERT INTO {FTS_TABLE}(rowid, content) VALUES(?, ?)", (i, content))
            cur.execute("INSERT INTO doc_metadata(rowid, file_name, position) VALUES(?, ?, ?)",
                        (i, file_name, str(sec.get('start_line', 0))))
            if progress and i % 200 == 0:
                progress(i, total)
        st = os.stat(source_path)
        info = {'source_name': file_name, 'source_size': str(st.st_size), 'source_mtime': str(int(st.st_mtime)),
                'separator': separator, 'topics': str(total), 'fts': '1' if use_fts else '0',
                'built_at': str(int(time.time()))}
        cur.executemany("INSERT INTO info(key, value) VALUES(?, ?)", list(info.items()))
        conn.commit()
        if progress:
            progress(total, total)
    except Exception:
        conn.close()
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    conn.close()
    os.replace(tmp, index_path)
    return total


def read_info(index_path: str) -> Dict[str, str]:
    """Сведения об индексе (пусто, если файла нет или он не нашего формата, например создан на ПК)."""
    if not os.path.exists(index_path):
        return {}
    try:
        conn = _connect(index_path)
        try:
            return {r[0]: r[1] for r in conn.execute("SELECT key, value FROM info")}
        finally:
            conn.close()
    except sqlite3.Error:
        return {}


def _candidate_terms(query_words_info) -> Tuple[List[str], bool]:
    """Самый короткий допустимый фрагмент каждого слова запроса: любой более длинный фрагмент (префикс слова)
       содержит его как подстроку, поэтому достаточно искать только его. Второе значение — можно ли обойтись
       FTS5 (триграммы работают от 3 знаков; для более коротких фрагментов нужен перебор)."""
    terms, fts_ok = [], True
    for word, min_len, _must_start in query_words_info:
        frags = get_fragments_with_levels(word, min_len)
        shortest = (frags[-1][0] if frags else word).lower()
        if len(shortest) < 3:
            fts_ok = False
        terms.append(shortest)
    return terms, fts_ok


class BookIndex:
    """Открытый индекс книги для поиска. Создавайте на каждый поиск и закрывайте (потоки не делят соединение)."""

    def __init__(self, path: str):
        self.path = path
        self.conn = _connect(path)
        info = {}
        try:
            info = {r[0]: r[1] for r in self.conn.execute("SELECT key, value FROM info")}
        except sqlite3.Error:
            pass
        self.info = info
        self.is_fts = self._detect_fts()

    def _detect_fts(self) -> bool:
        row = self.conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (FTS_TABLE,)).fetchone()
        return bool(row and row[0] and 'fts5' in row[0].lower())

    def close(self):
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    def topic_count(self) -> int:
        try:
            return self.conn.execute(f"SELECT count(*) FROM {FTS_TABLE}").fetchone()[0]
        except sqlite3.Error:
            return 0

    def search(self, query_words_info, candidate_limit: int = 3000) -> List[dict]:
        """Топики, где есть слова запроса или их начальные фрагменты (как в десктопной версии).
           Возвращает [{'rowid', 'content', 'meta', 'position'}] — без сортировки между книгами."""
        if not query_words_info:
            return []
        terms, fts_ok = _candidate_terms(query_words_info)
        rows = []
        if self.is_fts and fts_ok:
            match = " OR ".join('"%s"' % t.replace('"', '') for t in terms)
            try:
                rows = self.conn.execute(
                    f"SELECT rowid, content FROM {FTS_TABLE} WHERE {FTS_TABLE} MATCH ? LIMIT ?",
                    (match, candidate_limit)).fetchall()
            except sqlite3.Error:
                rows = self._scan(terms, candidate_limit)
        else:
            rows = self._scan(terms, candidate_limit)

        out = []
        positions = self._positions([r[0] for r in rows])
        for rowid, content in rows:
            meta = compute_matches(content, query_words_info)
            if meta is None:
                continue
            out.append({'rowid': rowid, 'content': content, 'meta': meta, 'position': positions.get(rowid, '0')})
        return out

    def _scan(self, terms: List[str], limit: int) -> List[Tuple[int, str]]:
        """Перебор без FTS5: фрагмент ищется в нижнем регистре (в SQLite LIKE не понимает кириллицу)."""
        res = []
        for rowid, content in self.conn.execute(f"SELECT rowid, content FROM {FTS_TABLE}"):
            low = content.lower()
            if any(t in low for t in terms):
                res.append((rowid, content))
                if len(res) >= limit:
                    break
        return res

    def _positions(self, rowids: List[int]) -> Dict[int, str]:
        if not rowids:
            return {}
        out = {}
        try:
            for i in range(0, len(rowids), 500):      # у SQLite ограничено число параметров в запросе
                chunk = rowids[i:i + 500]
                q = ','.join('?' * len(chunk))
                for r in self.conn.execute(f"SELECT rowid, position FROM doc_metadata WHERE rowid IN ({q})", chunk):
                    out[r[0]] = r[1]
        except sqlite3.Error:
            pass
        return out
