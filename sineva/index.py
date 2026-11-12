"""Индекс одной книги — файл SQLite, не зависящий от FTS5.

На телефонах в SQLite часто нет FTS5/trigram, поэтому поиск устроен иначе — «словарь + вхождения»:
  docs(rowid, content)            — текст топика (читается только для показанных карточек)
  doc_metadata(rowid, position)   — позиция топика в файле
  vocab(id, word)                 — все различные слова книги в нижнем регистре
  postings(word_id, topic_id, cnt)— в каком топике сколько раз встретилось слово
  info(key, value)                — сведения для проверки актуальности (в том числе format)
Запрос: фрагменты слов ищутся по словарю (в нём десятки тысяч слов, а не весь текст), для найденных слов
берутся вхождения — уровни совпадения и счётчики считаются без чтения текста, так что ранжирование
всех топиков занимает десятки миллисекунд даже на слабом телефоне.
"""
import os
import re
import sqlite3
import time
from typing import Callable, Dict, List, Optional, Tuple

from .queryparser import get_fragments_with_levels
from .splitter import split_into_sections_by_separator
from .textio import read_text_file

INDEX_FORMAT = '2'
DOCS_TABLE = "docs"
_TOKEN_RE = re.compile(r'[а-яА-ЯёЁa-zA-Z0-9]+')


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def build_index(source_path: str, index_path: str, separator: str,
                progress: Optional[Callable[[int, int], None]] = None) -> int:
    """Читает книгу, режет на топики и записывает индекс. Пишет во временный файл и заменяет им старый —
       прерванная индексация не оставляет испорченного индекса. Возвращает число топиков."""
    text = read_text_file(source_path)
    if not text.strip():
        raise ValueError("файл пуст или в нём нет текста")
    sections = split_into_sections_by_separator(text.splitlines(keepends=False), separator)

    tmp = index_path + '.building'
    if os.path.exists(tmp):
        os.remove(tmp)
    conn = _connect(tmp)
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode = OFF")
        cur.execute("PRAGMA synchronous = OFF")
        cur.execute(f"CREATE TABLE {DOCS_TABLE}(rowid INTEGER PRIMARY KEY, content TEXT)")
        cur.execute("CREATE TABLE doc_metadata(rowid INTEGER PRIMARY KEY, file_name TEXT, position TEXT)")
        cur.execute("CREATE TABLE vocab(id INTEGER PRIMARY KEY, word TEXT)")
        cur.execute("CREATE TABLE postings(word_id INTEGER, topic_id INTEGER, cnt INTEGER, "
                    "PRIMARY KEY(word_id, topic_id)) WITHOUT ROWID")
        cur.execute("CREATE TABLE info(key TEXT PRIMARY KEY, value TEXT)")
        file_name = os.path.basename(source_path)
        total = len(sections)
        word_ids: Dict[str, int] = {}
        cur.execute("BEGIN")
        for i, sec in enumerate(sections, 1):
            content = '\n'.join(sec['lines'])
            cur.execute(f"INSERT INTO {DOCS_TABLE}(rowid, content) VALUES(?, ?)", (i, content))
            cur.execute("INSERT INTO doc_metadata(rowid, file_name, position) VALUES(?, ?, ?)",
                        (i, file_name, str(sec.get('start_line', 0))))
            counts: Dict[str, int] = {}
            for token in _TOKEN_RE.findall(content):
                t = token.lower()
                counts[t] = counts.get(t, 0) + 1
            rows = []
            for word, cnt in counts.items():
                wid = word_ids.get(word)
                if wid is None:
                    wid = word_ids[word] = len(word_ids) + 1
                rows.append((wid, i, cnt))
            cur.executemany("INSERT INTO postings(word_id, topic_id, cnt) VALUES(?, ?, ?)", rows)
            if progress and i % 200 == 0:
                progress(i, total)
        cur.executemany("INSERT INTO vocab(id, word) VALUES(?, ?)", [(v, k) for k, v in word_ids.items()])
        st = os.stat(source_path)
        info = {'source_name': file_name, 'source_size': str(st.st_size), 'source_mtime': str(int(st.st_mtime)),
                'separator': separator, 'topics': str(total), 'format': INDEX_FORMAT,
                'words': str(len(word_ids)), 'built_at': str(int(time.time()))}
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
    """Сведения об индексе (пусто, если файла нет или он не нашего формата)."""
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


def _candidate_terms(query_words_info) -> List[str]:
    """Самый короткий допустимый фрагмент каждого слова запроса: любой более длинный фрагмент (префикс слова)
       содержит его как подстроку, поэтому по словарю достаточно искать только его."""
    terms = []
    for word, min_len, _must_start in query_words_info:
        frags = get_fragments_with_levels(word, min_len)
        terms.append((frags[-1][0] if frags else word).lower())
    return terms


class FastMatcher:
    """Результат тот же, что у queryparser.compute_matches, но быстрее: уровень совпадения считается
       один раз для каждого различного слова (кэш на весь поиск), а не для каждого вхождения в каждом топике.
       Это главное ускорение на телефоне."""

    def __init__(self, query_words_info):
        frags = []
        for word, min_len, must_start in query_words_info:
            exact_only = must_start and min_len is not None and min_len >= len(word)
            for frag, level in get_fragments_with_levels(word, min_len):
                frags.append((frag.lower(), level, must_start, exact_only))
        self._frags = frags
        self._memo = {}

    def _level(self, token_lower):
        best = 0
        for frag, level, must_start, exact_only in self._frags:
            if level <= best:
                continue
            if exact_only:
                ok = token_lower == frag
            elif must_start:
                ok = token_lower.startswith(frag)
            else:
                ok = frag in token_lower
            if ok:
                best = level
        return best

    def match(self, content):
        memo = self._memo
        counts = {i: 0 for i in range(5, 11)}
        matched = set()
        for token in _TOKEN_RE.findall(content):
            t = token.lower()
            lvl = memo.get(t)
            if lvl is None:
                lvl = memo[t] = self._level(t)
            if lvl:
                counts[lvl] += 1
                matched.add(t)
        if not matched:
            return None
        max_level = max(l for l, c in counts.items() if c > 0)
        return {'matched_words': list(matched), 'counts': counts,
                'max_level': max_level, 'count_at_max': counts[max_level]}


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
        self.is_current = info.get('format') == INDEX_FORMAT

    def close(self):
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    def topic_count(self) -> int:
        try:
            return int(self.info.get('topics') or self.conn.execute(f"SELECT count(*) FROM {DOCS_TABLE}").fetchone()[0])
        except (sqlite3.Error, ValueError):
            return 0

    def search(self, query_words_info, candidate_limit=None, cancelled=None) -> List[dict]:
        """Все топики, где есть слова запроса или их начальные фрагменты (как в десктопной версии).
           Возвращает [{'rowid', 'meta', 'content': None, 'position': None}] — текст и позиция
           подгружаются позже, только для показанных (fetch). Без сортировки между книгами."""
        if not query_words_info:
            return []
        if not self.is_current:
            raise ValueError("индекс старого формата — нажмите «Индексировать»")
        matcher = FastMatcher(query_words_info)
        terms = _candidate_terms(query_words_info)
        cond = ' OR '.join('instr(word, ?) > 0' for _ in terms)
        levels: Dict[int, Tuple[str, int]] = {}
        for wid, word in self.conn.execute(f"SELECT id, word FROM vocab WHERE {cond}", terms):
            lvl = matcher._level(word)
            if lvl:
                levels[wid] = (word, lvl)
        if not levels:
            return []
        topics: Dict[int, dict] = {}
        ids = list(levels)
        for i in range(0, len(ids), 500):            # у SQLite ограничено число параметров в запросе
            if cancelled and cancelled():
                return []
            chunk = ids[i:i + 500]
            q = ','.join('?' * len(chunk))
            for wid, topic, cnt in self.conn.execute(
                    f"SELECT word_id, topic_id, cnt FROM postings WHERE word_id IN ({q})", chunk):
                word, lvl = levels[wid]
                t = topics.get(topic)
                if t is None:
                    t = topics[topic] = {'counts': {k: 0 for k in range(5, 11)}, 'words': set()}
                t['counts'][lvl] += cnt
                t['words'].add(word)
        out = []
        for topic, t in topics.items():
            counts = t['counts']
            max_level = max(l for l, c in counts.items() if c > 0)
            out.append({'rowid': topic, 'content': None, 'position': None,
                        'meta': {'matched_words': list(t['words']), 'counts': counts,
                                 'max_level': max_level, 'count_at_max': counts[max_level]}})
        return out

    def read_range(self, start_rowid: int, limit: int, backward: bool = False) -> List[Tuple[int, str, str]]:
        """Последовательное чтение: топики от start_rowid вперёд (включая его) или, при backward,
           limit топиков перед ним. Возвращает [(rowid, content, position)] по возрастанию rowid."""
        if backward:
            cond, order = "d.rowid < ?", "DESC"
        else:
            cond, order = "d.rowid >= ?", "ASC"
        rows = self.conn.execute(
            f"SELECT d.rowid, d.content, m.position FROM {DOCS_TABLE} d LEFT JOIN doc_metadata m ON m.rowid = d.rowid "
            f"WHERE {cond} ORDER BY d.rowid {order} LIMIT ?", (start_rowid, limit)).fetchall()
        out = [(r[0], r[1], r[2] if r[2] is not None else '0') for r in rows]
        return out[::-1] if backward else out

    def ordinal(self, rowid: int) -> int:
        """Порядковый номер топика (1…N) по rowid."""
        return int(self.conn.execute(f"SELECT count(*) FROM {DOCS_TABLE} WHERE rowid <= ?", (rowid,)).fetchone()[0])

    def rowid_at(self, number: int) -> int:
        """rowid топика с порядковым номером number (1…N); 0, если такого нет."""
        r = self.conn.execute(f"SELECT rowid FROM {DOCS_TABLE} ORDER BY rowid LIMIT 1 OFFSET ?", (max(0, number - 1),)).fetchone()
        return int(r[0]) if r else 0

    def first_rowid(self) -> int:
        r = self.conn.execute(f"SELECT min(rowid) FROM {DOCS_TABLE}").fetchone()
        return int(r[0]) if r and r[0] is not None else 0

    def has_rowid_after(self, rowid: int) -> bool:
        return self.conn.execute(f"SELECT 1 FROM {DOCS_TABLE} WHERE rowid > ? LIMIT 1", (rowid,)).fetchone() is not None

    def fetch(self, rowids: List[int]) -> Dict[int, Tuple[str, str]]:
        """Текст и позиция выбранных топиков: {rowid: (content, position)}."""
        out: Dict[int, Tuple[str, str]] = {}
        for i in range(0, len(rowids), 500):
            chunk = rowids[i:i + 500]
            q = ','.join('?' * len(chunk))
            for r in self.conn.execute(
                    f"SELECT d.rowid, d.content, m.position FROM {DOCS_TABLE} d "
                    f"LEFT JOIN doc_metadata m ON m.rowid = d.rowid WHERE d.rowid IN ({q})", chunk):
                out[r[0]] = (r[1], r[2] if r[2] is not None else '0')
        return out
