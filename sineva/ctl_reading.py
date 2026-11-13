"""Вкладка «Чтение»: страницы выбранной книги подряд."""
import re
from .index import BookIndex
from .search import SearchOutcome


class ReadingMixin:
    """Вкладка «Чтение»: страницы выбранной книги подряд. Методы смешиваются в Controller."""

    # ================= вкладка «Чтение»: блоки выбранной книги подряд =================
    def _read_key(self, file_name: str) -> str:
        return 'read_pos:' + file_name.replace('=', '_')

    def set_tab(self, tab: str) -> None:
        """Переключение вкладок с карточками («Поиск» / «Чтение»): состояние каждой сохраняется."""
        if tab not in ('search', 'read'):
            return
        if tab != self.tab:
            self._states[self.tab] = dict(outcome=self.outcome, shown=self.shown, win_start=self.win_start,
                                          offsets=self.offsets, expanded=self.expanded, reading=self.reading)
            st = self._states.get(tab) or {}
            self.tab = tab
            self.outcome = st.get('outcome') or SearchOutcome()
            self.shown = st.get('shown', 0)
            self.win_start = st.get('win_start', 0)
            self.offsets = st.get('offsets') or {}
            self.expanded = st.get('expanded') or set()
            self.reading = st.get('reading')
        if tab == 'search':
            if (self.outcome.results or self.outcome.query) and set(self.selected) != getattr(self, 'outcome_books', set(self.selected)):
                self.outcome = SearchOutcome()                      # выбор книг изменился: старые результаты не показываем
                self.shown = self.win_start = 0
                self.offsets, self.expanded = {}, set()
                self.pending_append = None
                self.on_cards([], 0, False)
        if tab == 'read':
            books = self.selected_books()
            same = self.reading and len(books) == 1 and books[0].file_name == self.reading['book']
            if not same:
                self.reading = None
                self.outcome = SearchOutcome()
                self.offsets, self.expanded = {}, set()
                self.dirty.add('read')
                self.on_cards([], 0, False)
                self._start_reading()
                return
        if tab in self.dirty:
            self._publish()
        self.restate()

    def _start_reading(self) -> None:
        books = self.selected_books()
        if len(books) != 1:
            self.on_status("Чтение: выберите на вкладке «Книги» ровно одну книгу")
            return
        book = books[0]
        if not book.ready:
            self.on_status(f"«{book.display}»: книга не проиндексирована")
            return
        try:
            saved = int(self.config.get(self._read_key(book.file_name), '0') or 0)
        except ValueError:
            saved = 0
        idx = BookIndex(book.index_path)
        try:
            first = idx.first_rowid()
            number = idx.ordinal(saved) if saved > first else 0
        finally:
            idx.close()
        self.on_status(f"Выбрана книга {book.display}")
        if number > 1 and self.on_ask_continue:
            hist = self.read_history(book)
            idx = BookIndex(book.index_path)
            try:
                total = idx.topic_count()
            finally:
                idx.close()
            self.on_ask_continue(book.display, hist, lambda rowid: self._read_from(book, rowid),
                                 lambda n: self.read_page(book, n), total)
        elif number > 1:
            self._read_from(book, saved)
        else:
            self._read_from(book, first)

    def read_page(self, book, number: int) -> None:
        """Чтение с указанной страницы (1…N)."""
        idx = BookIndex(book.index_path)
        try:
            rowid = idx.rowid_at(max(1, int(number))) or idx.first_rowid()
        finally:
            idx.close()
        self._read_from(book, rowid)

    def _remember_read(self, file_name: str, rows) -> None:
        """Последние 10 прочитанных блоков книги (rowid, от старых к новым) — для окна «Продолжить»."""
        key = 'read_hist:' + file_name.replace('=', '_')
        old = [int(x) for x in (self.config.get(key, '') or '').split(',') if x.strip().isdigit()]
        ids = [r[0] for r in rows]
        merged = [x for x in old if x not in ids] + ids
        self.config.set(key, ','.join(str(x) for x in merged[-10:]))
        self.config.save()

    @staticmethod
    def _first_sentence(text: str) -> str:
        t = ' '.join((text or '').split())
        m = re.match(r'(.+?[.!?…])(\s|$)', t)
        out = m.group(1) if m else t
        return out if len(out) <= 110 else out[:107].rstrip() + '…'

    def read_history(self, book):
        """[(rowid, номер блока, первое предложение)] последних прочитанных блоков, последний — в конце."""
        key = 'read_hist:' + book.file_name.replace('=', '_')
        ids = [int(x) for x in (self.config.get(key, '') or '').split(',') if x.strip().isdigit()]
        try:
            saved = int(self.config.get(self._read_key(book.file_name), '0') or 0)
        except ValueError:
            saved = 0
        if saved and saved not in ids:
            ids.append(saved)
        ids = ids[-10:]
        idx = BookIndex(book.index_path)
        try:
            data = idx.fetch(ids)
            return [(i, idx.ordinal(i), self._first_sentence(data[i][0])) for i in ids if i in data]
        finally:
            idx.close()

    def _read_rows(self, book, start: int, step: int):
        idx = BookIndex(book.index_path)
        try:
            rows = idx.read_range(start, step)
            if not rows:
                return [], 0, False, 0
            return rows, idx.topic_count(), idx.has_rowid_after(rows[-1][0]), idx.ordinal(rows[0][0])
        finally:
            idx.close()

    @staticmethod
    def _read_items(book, rows, base):
        return [{'book': book.file_name, 'book_display': book.display, 'rowid': rid, 'content': content,
                 'position': pos, 'ordinal': base + i,
                 'meta': {'max_level': 0, 'count_at_max': 0, 'matched_words': []}}
                for i, (rid, content, pos) in enumerate(rows)]

    def _read_from(self, book, start: int) -> None:
        """Начинает чтение с блока rowid=start: показывается один блок, следующие подгружаются про запас."""
        rows, total, has_next, base = self._read_rows(book, start, self.config.get_int('page_size', 3))
        if not rows:
            self.on_status("В книге нет страниц")
            return
        outcome = SearchOutcome(query='')
        outcome.results = self._read_items(book, rows, base)
        self.outcome = outcome
        self.win_start = 0
        self.shown = 1
        self.reading = {'book': book.file_name, 'last': rows[-1][0], 'has_next': has_next, 'total': total,
                        'fresh': True}
        self.expanded.clear()
        self.offsets.clear()
        self._reading_seen(book, rows[0])
        self._publish()
        self.say(self.read_msgs())

    def _reading_seen(self, book, row) -> None:
        """Прочитанным считается показанный блок: от него будет предложено продолжить."""
        self.set_setting(self._read_key(book.file_name), row[0])
        self._remember_read(book.file_name, [row])

    def _read_load(self, book, after: bool) -> bool:
        """Подгрузка блоков за последним (after) или перед первым из уже загруженных."""
        r, res = self.reading, self.outcome.results
        step = max(3, self.config.get_int('page_size', 3))
        if after:
            if not r['has_next']:
                return False
            rows, total, has_next, base = self._read_rows(book, r['last'] + 1, step)
            if not rows:
                r['has_next'] = False
                return False
            res.extend(self._read_items(book, rows, base))
            r['last'], r['has_next'] = rows[-1][0], has_next
            return True
        first = res[0]
        if first.get('ordinal', 1) <= 1:
            return False
        lo = max(1, first['rowid'] - step)
        rows, total, has_next, base = self._read_rows(book, lo, first['rowid'] - lo)
        rows = [x for x in rows if x[0] < first['rowid']]
        if not rows:
            return False
        res[0:0] = self._read_items(book, rows, base)
        self.win_start += len(rows)
        return True

    def go(self, delta: int) -> None:
        """Листание: следующий (+1) или предыдущий (-1) блок — и в поиске, и в чтении."""
        if self.reading:
            book = self.library.find(self.reading['book'])
            if not book:
                return
            res = self.outcome.results
            new = self.win_start + delta
            if new >= len(res) and not self._read_load(book, True):
                return
            if new < 0:
                if not self._read_load(book, False):
                    return
                new = self.win_start + delta
            if not 0 <= new < len(self.outcome.results):
                return
            self.win_start = new
            item = self.outcome.results[new]
            self._reading_seen(book, (item['rowid'],))
            self.pending_append = None
            self._publish()
            self.say(self.read_msgs())
            self._prefetch_read(book)
            return
        items = self._items()
        new = self.win_start + delta
        if not 0 <= new < len(items):
            return
        self.win_start = new
        for j, it in enumerate(items):                      # память: текст держим только у текущего и соседних блоков
            if abs(j - new) > 2 and it.get('content') is not None:
                it['content'] = None
        self.expanded.clear()
        self.pending_append = None
        self._publish()
        self.search.fill(items[max(0, new - 1):new + 2])    # соседние блоки готовятся заранее

    def _prefetch_read(self, book) -> None:
        """Чтение: заранее подгружаем блоки за текущим, чтобы листание не ждало диск."""
        if self.win_start + 2 >= len(self.outcome.results) and self.reading['has_next']:
            self._read_load(book, True)

    def show_more(self) -> None:           # прежнее имя: теперь это просто «следующий блок»
        self.go(1)

    def toggle_expand(self, key: str) -> None:
        if key in self.expanded:
            self.expanded.discard(key)
        else:
            self.expanded.add(key)
        self._publish()
