"""Логика экранов без Kivy: состояние поиска, выбор книг, индексация, страницы результатов.

Интерфейс (Kivy) только подписывается на колбэки on_* и вызывает методы. Благодаря этому всё поведение
проверяется тестами на ПК. Колбэки вызываются через ui_post — в Kivy это Clock.schedule_once, в тестах — вызов на месте.
"""
import os
import threading
from typing import Callable, Dict, List, Optional

from . import marks as marks_mod
from .config import AppConfig
from .highlight import build_brief_view_mapped, get_intervals
from .library import Book, Library
from .paths import AppPaths
from .search import COLOR_LINK, SearchOutcome, SearchService
from .vector import VectorRegistry, available_types
from .ctl_marks import MarksMixin
from .ctl_export import ExportMixin
from .ctl_reading import ReadingMixin
from .ctl_sync import SyncMixin


def _noop(*_a, **_k):
    return None


class Controller(MarksMixin, ExportMixin, ReadingMixin, SyncMixin):
    def __init__(self, paths: Optional[AppPaths] = None, ui_post: Callable = None,
                 schedule: Callable = None, run_async: bool = True):
        """ui_post(fn)               — выполнить fn в потоке интерфейса;
           schedule(fn, delay_sec)   — вернуть объект с .cancel() и выполнить fn позже (для задержки ввода);
           run_async=False           — всё синхронно (тесты)."""
        self.paths = paths or AppPaths()
        self.config = AppConfig(self.paths.config_path)
        self.library = Library(self.paths, self.config)
        self.vectors = VectorRegistry(self.config, self.paths.vector_dir)
        self.search = SearchService(self.config, self.vectors)
        self.marks = marks_mod.MarksStore(self.paths.marks_db)
        self.ui_post = ui_post or (lambda fn: fn())
        self.schedule = schedule
        self.run_async = run_async

        self.mode = 'text'                      # 'text' | 'vector'
        self.query = ''
        self.outcome = SearchOutcome()
        self.shown = 0
        self.cap = 0                           # сколько «лучших» топиков сейчас доступно (растёт кнопкой)
        self.more_label = ''
        self.nav = {'pos': 0, 'total': 0, 'prev': False, 'next': False}
        self.offsets = {}                       # ключ топика -> с какого символа показывается окно длинного текста
        self.expanded = set()                   # ключи раскрытых (полный текст) топиков
        self.selected = self._load_selected()
        self._token = 0
        self._timer = None
        self.on_reset = lambda: None            # интерфейс очищает карточки обеих вкладок
        self.undo_stack = []                    # последние выделения: можно отменить кнопкой «Отменить выделение»
        self.on_undo = lambda n: None
        self.tab = 'search'                     # активная вкладка с карточками: 'search' | 'read'
        self._states = {}                       # сохранённые состояния неактивной вкладки
        self.dirty = set()                      # вкладки, карточки которых устарели (шрифт, вид) и перерисуются при входе
        self.search_ms = 0.0                    # время последнего поиска (для строки «Итого»)
        self.win_start = 0                      # индекс первой показанной карточки (окно ≤ WINDOW карточек)
        self.pending_append = None              # {'drop': n, 'new': m} — «Показать ещё»: интерфейс дорисовывает, а не перестраивает
        self.reading = None                     # режим чтения: {'book','last','has_next','total'}
        self.on_sync = None                     # (состояние 'start'|'done'|'error', текст) — окно «Синхронизация»
        self.on_ask_continue = None             # (название книги, [(rowid, номер, первое предложение)], продолжить(rowid), перейти(номер страницы), всего страниц)
        self._indexing_names = set()

        # колбэки интерфейса
        self.on_cards = _noop       # (cards: list[dict], total: int, has_more: bool)
        self.on_words = _noop       # (rows: list[dict])
        self.on_status = _noop      # (text: str)
        self.on_books = _noop       # (rows: list[dict])
        self.on_progress = _noop    # (book_name: str, done: int, total: int)

    # ================= выбор книг =================
    def _load_selected(self) -> set:
        names = [n for n in self.config.get('last_indices', '').split('|') if n]
        have = {b.file_name for b in self.library.books}
        sel = {n for n in names if n in have}
        return sel

    def _save_selected(self):
        self.config.set('last_indices', '|'.join(sorted(self.selected)))
        self.config.save()

    def selected_books(self) -> List[Book]:
        return [b for b in self.library.books if b.file_name in self.selected]

    def is_selected(self, file_name: str) -> bool:
        return file_name in self.selected

    def toggle_book(self, file_name: str) -> None:
        if file_name in self.selected:
            self.selected.discard(file_name)
        else:
            self.selected.add(file_name)
        self._save_selected()
        self.publish_books()
        self._sel_status()

    def select_all(self, value: bool) -> None:
        self.selected = {b.file_name for b in self.library.books if b.ready} if value else set()
        self._save_selected()
        self.publish_books()
        self._sel_status()

    def _sel_status(self) -> None:
        if self.tab == 'search' and not self.outcome.results:
            self.selection_status()

    def folder_sizes(self) -> dict:
        """Размеры папок в байтах: книги и индексы."""
        def size(path):
            total = 0
            for root, _dirs, files in os.walk(path):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
            return total
        return {'books': size(self.paths.books_dir), 'index': size(self.paths.index_dir)}

    def book_rows(self) -> List[dict]:
        rows = []
        for b in self.library.books:
            status = {'ready': f"индекс готов · топиков: {b.topics}",
                      'stale': f"индекс устарел · топиков: {b.topics}",
                      'not_indexed': "нужна индексация"}[b.state]
            rows.append({'file_name': b.file_name, 'display': b.display, 'separator': b.separator,
                         'status': status, 'state': b.state, 'selected': b.file_name in self.selected,
                         'size_kb': max(1, b.size // 1024)})
        return rows

    def rescan(self) -> None:
        """Тихо перечитывает папку книг и mconfig.ini: вызывается при открытии вкладки «Книги» и возврате в приложение."""
        self.library.scan()
        self.selected &= {b.file_name for b in self.library.books}
        self.publish_books()
        self._auto_index()

    def _auto_index(self) -> None:
        """Автоматическая переиндексация (настройка «Настройки»): новые и изменённые книги индексируются сами."""
        if not self.auto_index_on():
            return
        names = [b.file_name for b in self.library.books if b.state != 'ready']
        if names:
            self.index_books(names)

    def auto_index_on(self) -> bool:
        return self.config.get_bool('index_auto', False)

    def maybe_index(self, file_names: List[str]) -> bool:
        """Индексация после изменения книги: только в автоматическом режиме. В ручном — подсказка, без индексации."""
        if self.auto_index_on():
            self.index_books(file_names)
            return True
        self.on_status("Книга изменена. Режим индексации ручной — нажмите «Индексировать» на вкладке «Книги»")
        return False

    def publish_books(self):
        self.on_books(self.book_rows())

    def refresh(self) -> None:
        """Кнопка обновления: перечитывает mconfig.ini и папку книг (алиасы, новые файлы)."""
        self.library.scan()
        have = {b.file_name for b in self.library.books}
        self.selected &= have
        self.publish_books()
        self.on_status(f"Список обновлён: книг {len(self.library.books)}")

    # ================= индексация =================
    def index_books(self, file_names: List[str]) -> None:
        books = [self.library.find(n) for n in file_names if n not in self._indexing_names]
        books = [b for b in books if b]
        if not books:
            return
        self._indexing_names |= {b.file_name for b in books}       # одну книгу не индексируем дважды одновременно

        def work():
            for book in books:
                try:
                    self.ui_post(lambda b=book: self.on_status(f"Индексация: {b.display}…"))
                    self.library.index_book(book, lambda d, t, b=book: self.ui_post(
                        lambda: self.on_progress(b.display, d, t)))
                except Exception as e:
                    self.ui_post(lambda b=book, e=e: self.on_status(f"Ошибка индексации {b.display}: {e}"))
            def done():
                self._indexing_names -= {b.file_name for b in books}
                self.selected |= {b.file_name for b in books if b.ready}
                self._save_selected()
                self.publish_books()
                self.on_status("Индексация завершена")
            self.ui_post(done)
        self._run(work)

    def add_books(self, paths: List[str]) -> List[str]:
        added = []
        for p in paths:
            try:
                b = self.library.add_book_file(p)
                if b:
                    added.append(b.file_name)
                else:
                    self.on_status(f"Формат не поддерживается (нужны .txt или .docx): {os.path.basename(p)}")
            except Exception as e:
                self.on_status(f"Не удалось добавить {p}: {e}")
        self.publish_books()
        return added

    def remove_book(self, file_name: str, delete_file: bool = False) -> None:
        b = self.library.find(file_name)
        if b:
            self.library.remove_book(b, delete_file)
            self.selected.discard(file_name)
            self._save_selected()
            self.publish_books()

    def edit_book(self, file_name: str, display: str, separator: str) -> None:
        b = self.library.find(file_name)
        if b:
            self.library.update_book_settings(b, display, separator)
            self.publish_books()

    # ================= ввод запроса и поиск =================
    def on_query_changed(self, text: str) -> None:
        """Текст запроса только запоминается: поиск запускается кнопкой «Поиск» (или Enter)."""
        self.query = text

    def submit_query(self, text: str = None) -> None:
        """Кнопка «Поиск»: единственный способ запустить поиск."""
        if text is not None:
            self.query = text
        need = self.config.get_int('min_query_len', 3)
        if len(self.query.strip()) < need:
            self.on_status(f"Введите не менее {need} знаков")
            return
        self._start_search(self.query)

    def _start_search(self, text: str) -> None:
        self.search.only.clear()
        self.reading = None
        self._token += 1
        token = self._token
        books = self.selected_books()
        if not books:
            self.on_status("Выберите книги на вкладке «Книги»")
            return

        def work():
            outcome = self.search.search(text, books, self.mode, cancelled=lambda: token != self._token,
                                         count_words=self.on_words is not _noop)
            if token == self._token:
                self.ui_post(lambda: self._apply(outcome, token))
        self._run(work)

    def _fit(self, lengths) -> int:
        """Сколько блоков (из первых page_size) показать за раз: не больше page_size и page_chars символов
           суммарно, но минимум один (большой блок выводится один; очень большой режется на части при рисовании)."""
        limit = self.config.get_int('page_chars', 6000)
        n = total = 0
        for L in lengths[:self.config.get_int('page_size', 3)]:
            if n and total + L > limit:
                break
            total += L
            n += 1
        return max(1, n) if lengths else 0

    @staticmethod
    def _books_word(n: int) -> str:
        return 'книга' if n % 10 == 1 and n % 100 != 11 else ('книги' if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else 'книг')

    @staticmethod
    def _plural(n: int, one: str, few: str, many: str) -> str:
        return one if n % 10 == 1 and n % 100 != 11 else (few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many)

    def selection_status(self) -> None:
        """Статус до запроса: «Выбрана книга <алиас>» или «Выбрано 10 книг» (до трёх книг — по алиасам)."""
        books = self.selected_books()
        if not books:
            self.on_status("Выберите книги на вкладке «Книги»")
        elif len(books) == 1:
            self.on_status(f"Выбрана книга {books[0].display}")
        elif len(books) <= 3:
            self.on_status("Выбраны книги: " + ', '.join(b.display for b in books))
        else:
            n = len(books)
            self.on_status(f"Выбрано {n} {self._books_word(n)}")

    def _apply(self, outcome: SearchOutcome, token: int) -> None:
        if token != self._token:
            return
        self.outcome = outcome
        self.outcome_books = set(self.selected)
        self.win_start = 0
        self.shown = 1                                      # на экране всегда один блок; соседние готовятся заранее
        self.expanded.clear()
        self.offsets.clear()
        self._publish()
        visible = len(self.search.visible(outcome))
        if outcome.errors and not outcome.results:
            self.on_status('; '.join(outcome.errors))
        else:
            self.search_ms = outcome.elapsed_ms
            self.restate()

    # ================= результаты =================
    WINDOW = 20             # сколько карточек одновременно держим на экране и в памяти

    def short_status(self) -> bool:
        return self.config.get_bool('short_status', True)

    def search_msgs(self):
        """(полный, краткий) статус результатов поиска."""
        o = self.outcome
        books = self.selected_books()
        n = len(books)
        where = (f"в книге {books[0].display}" if n == 1 else
                 f"в {n} {self._plural(n, 'книге', 'книгах', 'книгах')}")
        frag = len(o.word_counts) or len(o.fragments)
        visible = len(self.search.visible(o))
        head = f"Поиск '{self.query.strip()}' {where}. Найдено {frag} "
        head += self._plural(frag, 'фрагмент', 'фрагмента', 'фрагментов')
        mode = '/'.join(sorted(o.modes)) or '—'
        full = (head + f" на {visible} стр. за {o.elapsed_ms:.0f} мс (индекс {o.index_ms:.0f} мс, {mode})"
                + (f" · {'; '.join(o.errors)}" if o.errors else ''))
        return full, head

    def read_msgs(self):
        res = self.outcome.results
        if not (self.reading and res):
            return None
        book = self.library.find(self.reading['book'])
        name = book.display if book else self.reading['book']
        item = res[min(self.win_start, len(res) - 1)]
        t = f"Выбрана книга {name}, стр. {item['ordinal']} из {self.reading['total']}"
        return t, t

    def say(self, pair) -> None:
        full, short = pair
        self.on_status(short if self.short_status() else full)

    def restate(self) -> None:
        """Статус текущей вкладки заново (после смены вкладки, настройки или фильтра)."""
        if self.tab == 'read':
            pair = self.read_msgs()
            if pair:
                self.say(pair)
            return
        o = self.outcome
        if o.errors and not o.results:
            self.on_status('; '.join(o.errors))
        elif o.results or o.query:
            self.say(self.search_msgs())
        else:
            self.selection_status()

    # ---------- переход на страницу и фильтр по словам ----------
    def page_info(self):
        """(текущая стр., всего стр.) активной вкладки."""
        if self.reading and self.outcome.results:
            return self.outcome.results[self.win_start]['ordinal'], self.reading['total']
        return self.win_start + 1, len(self._items())

    def goto_page(self, n: int) -> None:
        if self.reading:
            book = self.library.find(self.reading['book'])
            if book and 1 <= n <= self.reading['total']:
                self.read_page(book, n)
            return
        items = self._items()
        if 1 <= n <= len(items):
            self.go(n - 1 - self.win_start)

    def reset_filters(self) -> None:
        """Фильтр по словам действует только в поиске и сбрасывается при переходе к другим функциям."""
        if not self.search.only:
            return
        self.search.only.clear()
        if self.tab == 'search':
            self.win_start = 0
        elif self._states.get('search'):
            self._states['search']['win_start'] = 0
        self.dirty.add('search')

    def set_word_filter(self, words) -> None:
        self.search.only = set(words)
        self.win_start = 0
        self.expanded.clear()
        self._publish()
        self.restate()

    def _items(self):
        return self.outcome.results if self.reading else self.search.visible(self.outcome)

    def _build(self, light: bool = False):
        """Один показанный блок (текущий) и данные для листания: nav = {pos, total, prev, next}."""
        items = self._items()
        self.win_start = max(0, min(self.win_start, len(items) - 1))
        seg = items[self.win_start:self.win_start + 1]
        self.search.fill(seg)                              # текст подгружается только для показанного
        brief = self.config.get_bool('brief_view', False) and not self.reading
        cards = []
        for item in seg:
            key = f"{item['book']}:{item['rowid']}"
            expanded = key in self.expanded
            cards.append(self._card(self.win_start, key, item, brief and not expanded, expanded))
        if self.reading and seg:
            pos, total = seg[0].get('ordinal', self.win_start + 1), self.reading['total']
            nxt = self.win_start + 1 < len(items) or bool(self.reading['has_next'])
            prev = pos > 1
        else:
            pos, total = (self.win_start + 1 if seg else 0), len(items)
            nxt, prev = self.win_start + 1 < len(items), self.win_start > 0
        self.nav = {'pos': pos, 'total': total, 'prev': prev, 'next': nxt}
        return cards, len(items), self.nav

    def peek(self, delta: int):
        """Карточка соседнего блока (delta = ±1) для показа при перетаскивании страницы; None, если соседа нет.
           Позиция (win_start) при этом не меняется — кроме подгрузки блоков чтения слева, которая сдвигает индекс."""
        items = self._items()
        if self.reading:
            book = self.library.find(self.reading['book'])
            new = self.win_start + delta
            if book and new >= len(items):
                self._read_load(book, True)
            elif book and new < 0 and self._read_load(book, False):
                pass
            items = self._items()
        new = self.win_start + delta
        if not 0 <= new < len(items):
            return None
        item = items[new]
        self.search.fill([item])
        key = f"{item['book']}:{item['rowid']}"
        brief = self.config.get_bool('brief_view', False) and not self.reading
        return self._card(new, key, item, brief and key not in self.expanded, key in self.expanded)

    def full_cards(self):
        return self._build(False)[0]

    def _publish(self) -> None:
        self.dirty.discard(self.tab)
        self.dirty.add('read' if self.tab == 'search' else 'search')     # карточки другой вкладки теперь устарели
        cards, total, nav = self._build()
        self.on_cards(cards, total, nav)
        if self.on_words is not _noop:                 # панель слов отключена в интерфейсе — зря не считаем
            self.on_words(self.search.panel_words(self.outcome))

    def reset_views(self) -> None:
        """Сброс вкладок «Поиск» и «Чтение» (смена шрифта/вида): ничего не перерисовывается, результаты
           очищаются — быстрее, чем перестраивать до 20 карточек. Поиск и чтение запускаются заново."""
        self._token += 1
        self.search.only.clear()
        self._states.clear()
        self.outcome = SearchOutcome()
        self.shown = self.win_start = 0
        self.offsets, self.expanded = {}, set()
        self.reading = None
        self.pending_append = None
        self.more_label = ''
        self.nav = {'pos': 0, 'total': 0, 'prev': False, 'next': False}
        self.dirty.clear()
        self.undo_stack.clear()
        self.on_undo(0)
        self.on_reset()

    MAX_CHARS = 1800          # длиннее — показываем окном: огромная текстура на телефоне даёт чёрные прямоугольники и тормоза

    def _view(self, key: str, item: dict, brief: bool):
        """(текст на экране, интервалы подсветки, карта кусков, постраничность) для топика."""
        content = item['content']
        exact, frags = self.search.highlight_spec(self.outcome, item)
        if self.marks_only():
            text, segments = self._marks_text(item)
            return text, get_intervals(text, exact, frags), segments, None
        if brief:
            text, hl, segments = build_brief_view_mapped(content, exact, frags)
            return text, hl, segments, None
        return content, get_intervals(content, exact, frags), [(0, 0, len(content))], None

    def marks_only(self) -> bool:
        """Режим «Отметки»: показываются только ранее выделенные фрагменты (и мысли), менять отметки нельзя."""
        return self.config.get_bool('marks_view', False)

    def _marks_text(self, item: dict):
        """Текст из одних отмеченных фрагментов (подчёркнутое и слова с мыслями), склеенных « … », и карта кусков."""
        content, fkey = item['content'], marks_mod.file_key(item['book'])
        spans = [(s_, e_) for _m, s_, e_ in marks_mod.find_mark_spans(content, self.marks.marks_for_file(fkey))]
        spans += [(s_, e_) for _m, s_, e_ in marks_mod.find_mark_spans(content, self.marks.notes_for_file(fkey))]
        merged = []
        for s_, e_ in sorted(spans):
            if merged and s_ <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e_)
            else:
                merged.append([s_, e_])
        if not merged:
            return '(отметок нет)', []
        text, segs = '', []
        for i, (s_, e_) in enumerate(merged):
            if i:
                text += ' … '
            segs.append((len(text), s_, e_ - s_))
            text += content[s_:e_]
        return text, segs

    @classmethod
    def chunks(cls, text: str):
        """Длинный текст рисуется несколькими подряд идущими виджетами (одна огромная текстура на телефоне даёт
           чёрные прямоугольники): границы — по концу строки или слова. [(начало, конец)]."""
        out, n, pos = [], len(text), 0
        while pos < n:
            end = min(n, pos + cls.MAX_CHARS)
            if end < n:
                cut = max(text.rfind('\n', pos, end), text.rfind(' ', pos, end))
                if cut > pos + cls.MAX_CHARS // 2:
                    end = cut + 1
            out.append((pos, end))
            pos = end
        return out or [(0, 0)]

    def _card(self, i: int, key: str, item: dict, brief: bool, expanded: bool) -> dict:
        content = item['content']
        text, hl, segments, paging = self._view(key, item, brief)
        layers = self._layers(item, segments)
        spans = layers['marks']
        header = (f"[ref=page][b][color={COLOR_LINK}]Страница {item['ordinal']}[/color][/b][/ref] [size=12sp][color=777777]из {self.reading['total']}[/color][/size]") if self.reading and 'ordinal' in item \
            else self.search.header_markup(item, i + 1)
        if self.reading and 'ordinal' in item:
            h_kind, h_title = 'read', f"Страница {item['ordinal']}"
            h_sub = f"[color=777777]из {self.reading['total']}[/color]"
        else:
            h_kind, h_title = 'search', f"{i + 1}. {item.get('book_display', '')}"
            h_sub = (f"[color=777777]ур. {item['meta']['max_level']} · [/color]"
                     f"[ref=words][b][color={COLOR_LINK}]Слова[/color][/b][/ref]")
        return {'key': key, 'book': item['book'], 'header': header,
                'h_kind': h_kind, 'h_title': h_title, 'h_sub': h_sub,
                'h_level': item.get('meta', {}).get('max_level', 0),
                'text': text, 'hl': [tuple(x) for x in hl], 'marks': spans, 'segments': segments,
                'big': layers['big'], 'color': layers['color'], 'notes': layers['notes'],
                'expanded': expanded, 'chunks': self.chunks(text), 'source': self._source_label(item['book'])}

    def _source_label(self, file_name: str) -> str:
        book = self.library.find(file_name)
        return f"{file_name} ({book.display})" if book and book.display and book.display != file_name else file_name

    # ================= слова =================
    def toggle_word(self, word: str) -> None:
        """Клик по слову в панели: зачёркивание/возврат. Положение списка не меняется (его держит интерфейс),
           количество показанных карточек сохраняется."""
        self.search.toggle_word(word)
        self._publish()

    def exclude_all_words(self) -> None:
        rows = self.search.panel_words(self.outcome)
        all_excluded = bool(rows) and all(r['excluded'] for r in rows)
        self.search.set_all_excluded([r['word'] for r in rows], not all_excluded)
        self._publish()

    def word_rows(self) -> List[dict]:
        if not self.outcome.word_counts and self.outcome.results:
            self.outcome.word_counts = self.search._count_words(self.outcome.results)
        rows = self.search.panel_words(self.outcome)
        for r in rows:
            r['markup'] = self.search.word_markup(r)
        return rows

    # ================= настройки и векторы =================
    def set_setting(self, key: str, value) -> None:
        self.config.set(key, value)
        self.config.save()

    def vector_status(self) -> Dict[str, object]:
        """Для экрана настроек: что доступно в смысловом поиске."""
        return {'types': available_types(), 'models': [m.display_name for m in self.vectors.list()],
                'problems': list(self.vectors.problems),
                'ready': bool(self.vectors.list())}

    def set_mode(self, mode: str) -> None:
        self.mode = mode if mode in ('text', 'vector') else 'text'

    # ================= служебное =================
    def _run(self, fn: Callable) -> None:
        if self.run_async:
            threading.Thread(target=fn, daemon=True).start()
        else:
            fn()
