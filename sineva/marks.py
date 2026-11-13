"""Подчёркнутые фрагменты (мобильная версия; логика та же, что в десктопной Синеве+).

   Отметки хранятся ОТДЕЛЬНО от файлов-источников — в небольшом файле SQLite
   (marks.db рядом с mconfig.ini), одна строка на отметку. Запись в сами
   файлы была бы хуже: окно результатов показывает текст из индекса, а .docx
   читается как чистый текст (форматирование отбрасывается), так что
   подчёркивание в источнике Синева+ всё равно не показала бы; у .txt
   подчёркивания нет вовсе; правка источника меняет его текст, а программа не
   замечает, что файл изменился.

   ПРИВЯЗКА — К ТЕКСТУ, а не к смещению или номеру записи. Отметка хранит
   подчёркнутый фрагмент и около CONTEXT_CHARS символов окружения с каждой
   стороны. Один и тот же фрагмент находится и в окне результатов, и в
   "кратком" режиме, и при просмотре файла, и после переиндексации (номера
   записей в индексе при этом меняются, текст — нет). Все сравнения идут по
   тексту с СХЛОПНУТЫМИ пробелами: индексация .docx пропускает пустые абзацы, а
   просмотр файла их сохраняет, поэтому одно и то же место отличается
   количеством переводов строки.

   Отметка не теряется даже когда её не удаётся показать (текст в источнике
   изменили): она остаётся в базе и попадает в экспорт."""

import hashlib
import os
import re
import sqlite3
import threading
from collections import namedtuple
from datetime import datetime

CONTEXT_CHARS = 40
# Сколько символов окружения должно совпасть, чтобы фрагмент признали той самой
# отметкой. Без этого однословный фрагмент у границы блока, где окружение нечем
# проверить, подчёркивался бы во всех блоках файла, где встретилось такое слово.
MIN_VERIFIED_CONTEXT = 12

Mark = namedtuple('Mark', 'id file_key file_label fragment before after created')
# Слои поверх подчёркнутого (хранятся отдельно, привязаны к тексту так же, как отметки):
Style = namedtuple('Style', 'id file_key file_label kind fragment before after created')      # kind: 'big' | 'color'
Note = namedtuple('Note', 'id file_key file_label fragment before after text flags created')  # flags: буквы u, b, c
STYLE_KINDS = ('big', 'color')


def style_uid(kind: str, file_key_: str, fragment: str, before: str, after: str) -> str:
    return mark_uid(f"style:{kind}\x1e{file_key_}", fragment, before, after)


def note_uid(file_key_: str, fragment: str, before: str, after: str) -> str:
    return mark_uid(f"note\x1e{file_key_}", fragment, before, after)

_WS_RE = re.compile(r'\s+')
# Символы, недопустимые в XML (на нём построен .docx) — python-docx на них падает.
_XML_BAD_RE = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]')


def file_key(file_name) -> str:
    """Ключ файла: имя без папки, без учёта регистра. Один и тот же файл
       приходит и как "bibl.txt", и как "DB\\bibl.txt"."""
    return os.path.basename(str(file_name or '').replace('\\', '/')).strip().lower()


def mark_uid(file_key_: str, fragment: str, before: str, after: str) -> str:
    """Устойчивый идентификатор отметки (одинаков на телефоне и на ПК): по содержимому, а не по номеру строки."""
    raw = '\x1f'.join((file_key_ or '', fragment or '', before or '', after or ''))
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:20]


def _now() -> str:
    return datetime.now().isoformat(timespec='seconds')


def file_label(file_name) -> str:
    return os.path.basename(str(file_name or '').replace('\\', '/')).strip()


# ---------- нормализация пробелов ----------

def collapse_ws(text: str) -> str:
    return _WS_RE.sub(' ', text)


def normalize_with_map(text: str):
    """(norm, index_map): norm — текст с каждой серией пробельных символов,
       заменённой одним пробелом; index_map[i] — индекс в ИСХОДНОМ тексте
       символа norm[i] (для пробела — начала серии)."""
    parts, index_map, pos = [], [], 0
    for m in _WS_RE.finditer(text):
        if m.start() > pos:
            parts.append(text[pos:m.start()])
            index_map.extend(range(pos, m.start()))
        parts.append(' ')
        index_map.append(m.start())
        pos = m.end()
    if pos < len(text):
        parts.append(text[pos:])
        index_map.extend(range(pos, len(text)))
    return ''.join(parts), index_map


_NORM_CACHE = {}
_NORM_CACHE_MAX = 128
_NORM_CACHE_MAX_LEN = 100_000


def _normalized(content: str):
    """normalize_with_map с небольшим кэшем: одни и те же блоки показываются
       снова при листании страниц и переключении "Кратко/Развёрнуто"."""
    cached = _NORM_CACHE.get(content)
    if cached is not None:
        return cached
    result = normalize_with_map(content)
    if len(content) <= _NORM_CACHE_MAX_LEN:
        if len(_NORM_CACHE) >= _NORM_CACHE_MAX:
            _NORM_CACHE.pop(next(iter(_NORM_CACHE)))
        _NORM_CACHE[content] = result
    return result


# ---------- поиск отметок в тексте ----------

def trim_span(content: str, start: int, end: int):
    """Сдвигает границы так, чтобы по краям не было пробельных символов."""
    while start < end and content[start].isspace():
        start += 1
    while end > start and content[end - 1].isspace():
        end -= 1
    return start, end


def span_fields(content: str, start: int, end: int):
    """(fragment, before, after) для сохранения отметки по отрезку текста
       content[start:end] либо None, если там только пробелы."""
    start, end = trim_span(content, start, end)
    if start >= end:
        return None
    return (collapse_ws(content[start:end]),
            collapse_ws(content[max(0, start - CONTEXT_CHARS):start]),
            collapse_ws(content[end:end + CONTEXT_CHARS]))


def _side_overlap(available: str, stored: str, before_side: bool):
    """Сколько символов окружения подтверждено либо None, если окружение не
       сходится. Допускается, что одно короче другого: блок обрезает окружение
       по своей границе (топик — кусок файла, а отметку могли сделать при
       просмотре всего файла, и наоборот)."""
    if before_side:
        if available.endswith(stored):
            return len(stored)
        if stored.endswith(available):
            return len(available)
    else:
        if available.startswith(stored):
            return len(stored)
        if stored.startswith(available):
            return len(available)
    return None


def _context_matches(norm: str, start: int, end: int, before: str, after: str) -> bool:
    ob = _side_overlap(norm[max(0, start - CONTEXT_CHARS):start], before, True)
    oa = _side_overlap(norm[end:end + CONTEXT_CHARS], after, False)
    if ob is None or oa is None:
        return False
    return ob + oa >= min(MIN_VERIFIED_CONTEXT, len(before) + len(after))


def find_mark_spans(content: str, marks):
    """[(mark, start, end)] — где в тексте content находятся отметки (границы в
       координатах ИСХОДНОГО content). Отметка, не найденная в этом тексте,
       просто пропускается."""
    if not marks or not content:
        return []
    norm, index_map = _normalized(content)
    found = []
    for mark in marks:
        fragment = mark.fragment
        if not fragment:
            continue
        pos = norm.find(fragment)
        while pos != -1:
            end = pos + len(fragment)
            if _context_matches(norm, pos, end, mark.before, mark.after):
                found.append((mark, index_map[pos], index_map[end - 1] + 1))
            pos = norm.find(fragment, pos + 1)
    return found


# ---------- перевод между текстом на экране и исходным текстом ----------
# На экране может быть не весь текст блока: "краткий" режим показывает куски
# (segments = [(начало_на_экране, начало_в_тексте, длина)]), склеенные " … ".

def display_span_to_content(segments, d_start: int, d_end: int):
    out = []
    for d0, c0, length in segments:
        lo, hi = max(d_start, d0), min(d_end, d0 + length)
        if lo < hi:
            out.append((c0 + lo - d0, c0 + hi - d0))
    return out


def content_span_to_display(segments, c_start: int, c_end: int):
    out = []
    for d0, c0, length in segments:
        lo, hi = max(c_start, c0), min(c_end, c0 + length)
        if lo < hi:
            out.append((d0 + lo - c0, d0 + hi - c0))
    return out


# ---------- "Ч" как в Word: поставить или снять ----------

def analyze_span(content: str, located, c_start: int, c_end: int):
    """Разбор выделенного отрезка: (start, end, пересекающиеся_отметки,
       целиком_подчёркнут) либо None, если выделены одни пробелы. "Целиком"
       пробелы между подчёркнутыми словами не учитываются — иначе выделение,
       захватившее два соседних подчёркнутых слова вместе с пробелом между ними,
       не считалось бы подчёркнутым."""
    cs, ce = trim_span(content, c_start, c_end)
    if cs >= ce:
        return None
    overlapping = [(m, s, e) for m, s, e in located if s < ce and e > cs]
    covered = bytearray(ce - cs)
    for _m, s, e in overlapping:
        lo, hi = max(s, cs) - cs, min(e, ce) - cs
        covered[lo:hi] = b'\x01' * (hi - lo)
    fully = bool(overlapping) and all(covered[i] or content[cs + i].isspace() for i in range(ce - cs))
    return cs, ce, overlapping, fully


def plan_remove(content: str, cs: int, ce: int, overlapping):
    """(id_для_удаления, отрезки_для_добавления): подчёркивание снимается
       только с выделенного — хвосты отметок, выходящие за выделение, остаются."""
    delete_ids = [m.id for m, _s, _e in overlapping]
    keep = []
    for _m, s, e in overlapping:
        if s < cs:
            keep.append((s, cs))
        if e > ce:
            keep.append((ce, e))
    keep = [(a, b) for a, b in keep if trim_span(content, a, b)[0] < trim_span(content, a, b)[1]]
    return delete_ids, keep


def plan_add(cs: int, ce: int, overlapping):
    """Новая отметка сливается с пересекающимися: одна на весь объединённый отрезок."""
    start = min([cs] + [s for _m, s, _e in overlapping])
    end = max([ce] + [e for _m, _s, e in overlapping])
    return [m.id for m, _s, _e in overlapping], [(start, end)]


# ---------- хранилище ----------

class MarksStore:
    """Отметки в SQLite. Соединение открывается при первом обращении — если
       файл базы недоступен (папка только для чтения), программа запускается и
       показывает результаты как обычно, а ошибка проявится только при попытке
       подчеркнуть."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn = None
        self._lock = threading.RLock()
        self._cache = {}  # file_key -> [Mark]; сбрасывается при любой записи в этот файл
        self._scache = {}   # file_key -> [Style]
        self._ncache = {}   # file_key -> [Note]

    def _connect(self):
        if self._conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.execute("CREATE TABLE IF NOT EXISTS marks ("
                         "id INTEGER PRIMARY KEY AUTOINCREMENT, file_key TEXT NOT NULL, "
                         "file_label TEXT, fragment TEXT NOT NULL, before TEXT, after TEXT, created TEXT)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_marks_file ON marks(file_key)")
            conn.execute("CREATE TABLE IF NOT EXISTS tombstones (uid TEXT PRIMARY KEY, at TEXT NOT NULL)")   # снятые отметки (для синхронизации)
            conn.execute("CREATE TABLE IF NOT EXISTS styles ("
                         "id INTEGER PRIMARY KEY AUTOINCREMENT, file_key TEXT NOT NULL, file_label TEXT, kind TEXT NOT NULL, "
                         "fragment TEXT NOT NULL, before TEXT, after TEXT, created TEXT)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_styles_file ON styles(file_key)")
            conn.execute("CREATE TABLE IF NOT EXISTS notes ("
                         "id INTEGER PRIMARY KEY AUTOINCREMENT, file_key TEXT NOT NULL, file_label TEXT, "
                         "fragment TEXT NOT NULL, before TEXT, after TEXT, text TEXT, flags TEXT, created TEXT)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_file ON notes(file_key)")
            conn.commit()
            self._conn = conn
        return self._conn

    def marks_for_file(self, key: str):
        with self._lock:
            if key not in self._cache:
                rows = self._connect().execute(
                    "SELECT id, file_key, file_label, fragment, before, after, created "
                    "FROM marks WHERE file_key = ? ORDER BY id", (key,)).fetchall()
                self._cache[key] = [Mark(*row) for row in rows]
            return self._cache[key]

    def add_mark(self, file_name, fragment: str, before: str, after: str) -> int:
        with self._lock:
            conn = self._connect()
            key = file_key(file_name)
            cur = conn.execute(
                "INSERT INTO marks (file_key, file_label, fragment, before, after, created) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (key, file_label(file_name), fragment, before, after,
                 datetime.now().isoformat(timespec='seconds')))
            conn.execute("DELETE FROM tombstones WHERE uid = ?", (mark_uid(key, fragment, before, after),))
            conn.commit()
            self._cache.pop(key, None)
            return cur.lastrowid

    def delete_marks(self, ids):
        ids = list(ids)
        if not ids:
            return
        with self._lock:
            conn = self._connect()
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                marks_sql = ",".join("?" * len(chunk))
                for key, frag, bef, aft in conn.execute(
                        f"SELECT file_key, fragment, before, after FROM marks WHERE id IN ({marks_sql})", chunk).fetchall():
                    self._cache.pop(key, None)
                    conn.execute("INSERT OR REPLACE INTO tombstones (uid, at) VALUES (?, ?)",
                                 (mark_uid(key, frag, bef, aft), _now()))
                conn.execute(f"DELETE FROM marks WHERE id IN ({marks_sql})", chunk)
            conn.commit()

    def all_marks(self):
        with self._lock:
            rows = self._connect().execute(
                "SELECT id, file_key, file_label, fragment, before, after, created "
                "FROM marks ORDER BY file_key, id").fetchall()
            return [Mark(*row) for row in rows]

    # ---------- слои: размер, цвет, мысли ----------
    def styles_for_file(self, key: str, kind: str = None):
        with self._lock:
            if key not in self._scache:
                rows = self._connect().execute(
                    "SELECT id, file_key, file_label, kind, fragment, before, after, created "
                    "FROM styles WHERE file_key = ? ORDER BY id", (key,)).fetchall()
                self._scache[key] = [Style(*r) for r in rows]
            items = self._scache[key]
            return [x for x in items if x.kind == kind] if kind else items

    def add_style(self, file_name, kind: str, fragment: str, before: str, after: str) -> int:
        with self._lock:
            conn = self._connect()
            key = file_key(file_name)
            cur = conn.execute("INSERT INTO styles (file_key, file_label, kind, fragment, before, after, created) "
                               "VALUES (?, ?, ?, ?, ?, ?, ?)",
                               (key, file_label(file_name), kind, fragment, before, after, _now()))
            conn.execute("DELETE FROM tombstones WHERE uid = ?", (style_uid(kind, key, fragment, before, after),))
            conn.commit()
            self._scache.pop(key, None)
            return cur.lastrowid

    def delete_styles(self, ids):
        ids = list(ids)
        if not ids:
            return
        with self._lock:
            conn = self._connect()
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                q = ",".join("?" * len(chunk))
                for key, kind, frag, bef, aft in conn.execute(
                        f"SELECT file_key, kind, fragment, before, after FROM styles WHERE id IN ({q})", chunk).fetchall():
                    self._scache.pop(key, None)
                    conn.execute("INSERT OR REPLACE INTO tombstones (uid, at) VALUES (?, ?)",
                                 (style_uid(kind, key, frag, bef, aft), _now()))
                conn.execute(f"DELETE FROM styles WHERE id IN ({q})", chunk)
            conn.commit()

    def notes_for_file(self, key: str):
        with self._lock:
            if key not in self._ncache:
                rows = self._connect().execute(
                    "SELECT id, file_key, file_label, fragment, before, after, text, flags, created "
                    "FROM notes WHERE file_key = ? ORDER BY id", (key,)).fetchall()
                self._ncache[key] = [Note(*r) for r in rows]
            return self._ncache[key]

    def add_note(self, file_name, fragment: str, before: str, after: str, text: str, flags: str = '') -> int:
        with self._lock:
            conn = self._connect()
            key = file_key(file_name)
            cur = conn.execute("INSERT INTO notes (file_key, file_label, fragment, before, after, text, flags, created) "
                               "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                               (key, file_label(file_name), fragment, before, after, text, flags, _now()))
            conn.execute("DELETE FROM tombstones WHERE uid = ?", (note_uid(key, fragment, before, after),))
            conn.commit()
            self._ncache.pop(key, None)
            return cur.lastrowid

    def get_note(self, nid: int):
        with self._lock:
            row = self._connect().execute(
                "SELECT id, file_key, file_label, fragment, before, after, text, flags, created "
                "FROM notes WHERE id = ?", (nid,)).fetchone()
            return Note(*row) if row else None

    def update_note(self, nid: int, text: str = None, flags: str = None) -> None:
        with self._lock:
            note = self.get_note(nid)
            if note is None:
                return
            conn = self._connect()
            conn.execute("UPDATE notes SET text = ?, flags = ?, created = ? WHERE id = ?",
                         (note.text if text is None else text, note.flags if flags is None else flags, _now(), nid))
            conn.commit()
            self._ncache.pop(note.file_key, None)

    def delete_note(self, nid: int) -> None:
        with self._lock:
            note = self.get_note(nid)
            if note is None:
                return
            conn = self._connect()
            conn.execute("INSERT OR REPLACE INTO tombstones (uid, at) VALUES (?, ?)",
                         (note_uid(note.file_key, note.fragment, note.before, note.after), _now()))
            conn.execute("DELETE FROM notes WHERE id = ?", (nid,))
            conn.commit()
            self._ncache.pop(note.file_key, None)

    def all_notes(self):
        with self._lock:
            rows = self._connect().execute(
                "SELECT id, file_key, file_label, fragment, before, after, text, flags, created "
                "FROM notes ORDER BY file_key, id").fetchall()
            return [Note(*r) for r in rows]

    def snapshot_file(self, file_name):
        """Состояние всех слоёв файла (подчёркивания, размер, цвет, мысли) — для отмены последних действий."""
        with self._lock:
            key = file_key(file_name)
            return {'marks': list(self.marks_for_file(key)), 'styles': list(self.styles_for_file(key)),
                    'notes': list(self.notes_for_file(key))}

    def restore_file(self, file_name, snap):
        with self._lock:
            conn = self._connect()
            key = file_key(file_name)
            now = _now()

            def swap(table, cols, old_rows, new_rows, uid_of):
                old = {uid_of(r) for r in old_rows}
                keep = {uid_of(r) for r in new_rows}
                for uid in old - keep:
                    conn.execute("INSERT OR REPLACE INTO tombstones (uid, at) VALUES (?, ?)", (uid, now))
                for uid in keep:
                    conn.execute("DELETE FROM tombstones WHERE uid = ?", (uid,))
                conn.execute(f"DELETE FROM {table} WHERE file_key = ?", (key,))
                ph = ",".join("?" * len(cols))
                for r in new_rows:
                    conn.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({ph})", tuple(getattr(r, c) for c in cols))

            swap('marks', ('file_key', 'file_label', 'fragment', 'before', 'after', 'created'),
                 self.marks_for_file(key), snap['marks'], lambda r: mark_uid(r.file_key, r.fragment, r.before, r.after))
            swap('styles', ('file_key', 'file_label', 'kind', 'fragment', 'before', 'after', 'created'),
                 self.styles_for_file(key), snap['styles'],
                 lambda r: style_uid(r.kind, r.file_key, r.fragment, r.before, r.after))
            swap('notes', ('file_key', 'file_label', 'fragment', 'before', 'after', 'text', 'flags', 'created'),
                 self.notes_for_file(key), snap['notes'], lambda r: note_uid(r.file_key, r.fragment, r.before, r.after))
            conn.commit()
            self._cache.pop(key, None)
            self._scache.pop(key, None)
            self._ncache.pop(key, None)

    # ---------- синхронизация слоёв (файл sineva_extra.json; десктопу не нужен) ----------
    def extra_snapshot(self):
        """([записи слоёв как словари], {uid: время снятия}) — стили и мысли."""
        with self._lock:
            conn = self._connect()
            recs = []
            for r in conn.execute("SELECT id, file_key, file_label, kind, fragment, before, after, created FROM styles").fetchall():
                st = Style(*r)
                recs.append({'uid': style_uid(st.kind, st.file_key, st.fragment, st.before, st.after), 'type': 'style',
                             'kind': st.kind, 'file_key': st.file_key, 'file_label': st.file_label or '',
                             'fragment': st.fragment, 'before': st.before or '', 'after': st.after or '',
                             'created': st.created or ''})
            for nt in self.all_notes():
                recs.append({'uid': note_uid(nt.file_key, nt.fragment, nt.before, nt.after), 'type': 'note',
                             'file_key': nt.file_key, 'file_label': nt.file_label or '', 'fragment': nt.fragment,
                             'before': nt.before or '', 'after': nt.after or '', 'text': nt.text or '',
                             'flags': nt.flags or '', 'created': nt.created or ''})
            tombs = dict(conn.execute("SELECT uid, at FROM tombstones").fetchall())
            return recs, tombs

    def extra_apply(self, add, remove_uids, tombs):
        with self._lock:
            conn = self._connect()
            gone = set(remove_uids)
            if gone:
                for r in conn.execute("SELECT id, file_key, kind, fragment, before, after FROM styles").fetchall():
                    if style_uid(r[2], r[1], r[3], r[4], r[5]) in gone:
                        conn.execute("DELETE FROM styles WHERE id = ?", (r[0],))
                for r in conn.execute("SELECT id, file_key, fragment, before, after FROM notes").fetchall():
                    if note_uid(r[1], r[2], r[3], r[4]) in gone:
                        conn.execute("DELETE FROM notes WHERE id = ?", (r[0],))
            for m in add:
                if m.get('type') == 'note':
                    conn.execute("INSERT INTO notes (file_key, file_label, fragment, before, after, text, flags, created) "
                                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                 (m['file_key'], m.get('file_label', ''), m['fragment'], m.get('before', ''),
                                  m.get('after', ''), m.get('text', ''), m.get('flags', ''), m.get('created', '')))
                else:
                    conn.execute("INSERT INTO styles (file_key, file_label, kind, fragment, before, after, created) "
                                 "VALUES (?, ?, ?, ?, ?, ?, ?)",
                                 (m['file_key'], m.get('file_label', ''), m['kind'], m['fragment'], m.get('before', ''),
                                  m.get('after', ''), m.get('created', '')))
            conn.execute("DELETE FROM tombstones")
            for uid, at in tombs.items():
                conn.execute("INSERT INTO tombstones (uid, at) VALUES (?, ?)", (uid, at))
            conn.commit()
            self._scache.clear()
            self._ncache.clear()

    # ---------- синхронизация ----------
    def sync_snapshot(self):
        """(список отметок как словари, {uid: время снятия}) — то, что уходит в общий файл."""
        with self._lock:
            marks = [{'uid': mark_uid(m.file_key, m.fragment, m.before, m.after), 'file_key': m.file_key,
                      'file_label': m.file_label, 'fragment': m.fragment, 'before': m.before or '',
                      'after': m.after or '', 'created': m.created or ''} for m in self.all_marks()]
            tombs = dict(self._connect().execute("SELECT uid, at FROM tombstones").fetchall())
            return marks, tombs

    def sync_apply(self, add, remove_uids, tombs):
        """Применяет результат слияния: добавляет чужие отметки, убирает снятые, заменяет таблицу следов."""
        with self._lock:
            conn = self._connect()
            gone = set(remove_uids)
            if gone:
                for rid, key, frag, bef, aft in conn.execute(
                        "SELECT id, file_key, fragment, before, after FROM marks").fetchall():
                    if mark_uid(key, frag, bef, aft) in gone:
                        conn.execute("DELETE FROM marks WHERE id = ?", (rid,))
            for m in add:
                conn.execute("INSERT INTO marks (file_key, file_label, fragment, before, after, created) "
                             "VALUES (?, ?, ?, ?, ?, ?)",
                             (m['file_key'], m.get('file_label', ''), m['fragment'], m.get('before', ''),
                              m.get('after', ''), m.get('created', '')))
            conn.execute("DELETE FROM tombstones")
            for uid, at in tombs.items():
                conn.execute("INSERT INTO tombstones (uid, at) VALUES (?, ?)", (uid, at))
            conn.commit()
            self._cache.clear()
            self._scache.clear()
            self._ncache.clear()

    def close(self):
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
            self._cache.clear()
            self._scache.clear()
            self._ncache.clear()


# ---------- экспорт (без python-docx: на телефоне его нет) ----------

def marks_as_text(marks) -> str:
    """Все подчёркнутые фрагменты одним текстом: раздел на файл, фрагмент с окружением."""
    groups = {}
    for mark in marks:
        groups.setdefault(mark.file_key, []).append(mark)
    lines = [f"Подчёркнутые фрагменты. Создано: {datetime.now().strftime('%d.%m.%Y %H:%M')}. Фрагментов: {len(marks)}.", ""]
    for key in sorted(groups, key=lambda k: (groups[k][0].file_label or k).casefold()):
        items = groups[key]
        lines.append(f"=== {items[0].file_label or key} ===")
        for number, mark in enumerate(items, 1):
            before = ("…" + mark.before) if (mark.before or '').strip() else ''
            after = (mark.after + "…") if (mark.after or '').strip() else ''
            lines.append(f"{number}. {before}[{mark.fragment}]{after}")
        lines.append("")
    return "\n".join(lines)


def notes_as_text(notes) -> str:
    """Все свои мысли одним текстом: раздел на книгу, фрагмент с окружением и сама мысль."""
    groups = {}
    for nt in notes:
        groups.setdefault(nt.file_key, []).append(nt)
    lines = [f"Мои мысли. Создано: {datetime.now().strftime('%d.%m.%Y %H:%M')}. Мыслей: {len(notes)}.", ""]
    for key in sorted(groups, key=lambda k: (groups[k][0].file_label or k).casefold()):
        lines += [f"=== {groups[key][0].file_label or key} ===", ""]
        for nt in groups[key]:
            lines += [f"…{nt.before or ''} [{nt.fragment}] {nt.after or ''}…", f"Мысль: {nt.text or ''}", ""]
    return "\n".join(lines)


def export_notes_txt(notes, path: str):
    _atomic_write_bytes(path, notes_as_text(notes).encode('utf-8'))


def export_marks_txt(marks, path: str):
    _atomic_write_bytes(path, marks_as_text(marks).encode('utf-8'))


def _atomic_write_bytes(path: str, data: bytes):
    tmp = path + '.part'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def export_marks_docx(marks, path: str):
    """Минимальный .docx без внешних библиотек: по разделу на файл, подчёркнутый фрагмент подчёркнут,
       окружение серым."""
    import zipfile
    from xml.sax.saxutils import escape

    def clean(text):
        return escape(_XML_BAD_RE.sub('', text or ''))

    def run(text, underline=False, grey=False):
        props = ('<w:u w:val="single"/>' if underline else '') + ('<w:color w:val="808080"/>' if grey else '')
        rpr = f'<w:rPr>{props}</w:rPr>' if props else ''
        return f'<w:r>{rpr}<w:t xml:space="preserve">{clean(text)}</w:t></w:r>'

    def para(*runs, bold=False):
        ppr = '<w:pPr><w:pStyle w:val="Heading2"/></w:pPr>' if bold else ''
        return f'<w:p>{ppr}{"".join(runs)}</w:p>'

    groups = {}
    for mark in marks:
        groups.setdefault(mark.file_key, []).append(mark)
    body = [para(run("Подчёркнутые фрагменты"), bold=True),
            para(run(f"Создано: {datetime.now().strftime('%d.%m.%Y %H:%M')}. Фрагментов: {len(marks)}."))]
    for key in sorted(groups, key=lambda k: (groups[k][0].file_label or k).casefold()):
        items = groups[key]
        body.append(para(run(items[0].file_label or key), bold=True))
        for number, mark in enumerate(items, 1):
            runs = [run(f"{number}. ")]
            if (mark.before or '').strip():
                runs.append(run("…" + mark.before, grey=True))
            runs.append(run(mark.fragment, underline=True))
            if (mark.after or '').strip():
                runs.append(run(mark.after + "…", grey=True))
            body.append(para(*runs))
    ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<w:document xmlns:w="{ns}"><w:body>{"".join(body)}</w:body></w:document>')
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                     '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
    tmp = path + '.part'
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', content_types)
        z.writestr('_rels/.rels', rels)
        z.writestr('word/document.xml', document)
    os.replace(tmp, path)
