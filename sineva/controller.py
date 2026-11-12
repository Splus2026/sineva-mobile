"""Логика экранов без Kivy: состояние поиска, выбор книг, индексация, страницы результатов.

Интерфейс (Kivy) только подписывается на колбэки on_* и вызывает методы. Благодаря этому всё поведение
проверяется тестами на ПК. Колбэки вызываются через ui_post — в Kivy это Clock.schedule_once, в тестах — вызов на месте.
"""
import os
import re
import shutil
import threading
import time
from typing import Callable, Dict, List, Optional

from . import marks as marks_mod
from . import notes
from . import syncmerge
from .config import AppConfig
from .splitter import split_into_sections_by_separator
from .textio import read_text_file
from .highlight import build_brief_view_mapped, get_intervals
from .index import BookIndex
from .library import Book, Library
from .paths import AppPaths
from .search import SearchOutcome, SearchService, escape_markup
from .vector import VectorRegistry, available_types


def _noop(*_a, **_k):
    return None


class Controller:
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
            mode = '/'.join(sorted(outcome.modes)) or '—'
            books = self.selected_books()
            n = len(books)
            where = (f"в книге {books[0].display}" if n == 1 else
                     f"в {n} {self._plural(n, 'книге', 'книгах', 'книгах')}")
            frag = len(outcome.word_counts) or len(outcome.fragments)
            self.search_ms = outcome.elapsed_ms
            self.on_status(f"Поиск '{self.query.strip()}' {where}. Найдено {frag} "
                           f"{self._plural(frag, 'фрагмент', 'фрагмента', 'фрагментов')} на {visible} "
                           f"{self._plural(visible, 'странице', 'страницах', 'страницах')} за {outcome.elapsed_ms:.0f} мс "
                           f"(индекс {outcome.index_ms:.0f} мс, {mode})"
                           + (f" · {'; '.join(outcome.errors)}" if outcome.errors else ''))

    # ================= результаты =================
    WINDOW = 20             # сколько карточек одновременно держим на экране и в памяти

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

    def _release(self, items) -> None:
        """Выгрузка из памяти карточек, ушедших за пределы окна."""
        for it in items:
            key = f"{it['book']}:{it['rowid']}"
            self.expanded.discard(key)
            self.offsets.pop(key, None)
            it['content'] = None

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
        header = (f"[b]Страница {item['ordinal']}[/b] [size=12sp][color=777777]из {self.reading['total']} · "
                  f"поз. {escape_markup(str(item.get('position', '')))}[/color][/size]") if self.reading and 'ordinal' in item \
            else self.search.header_markup(item, i + 1)
        return {'key': key, 'book': item['book'], 'header': header,
                'text': text, 'hl': [tuple(x) for x in hl], 'marks': spans, 'segments': segments,
                'big': layers['big'], 'color': layers['color'], 'notes': layers['notes'],
                'expanded': expanded, 'chunks': self.chunks(text), 'source': self._source_label(item['book'])}

    def _source_label(self, file_name: str) -> str:
        book = self.library.find(file_name)
        return f"{file_name} ({book.display})" if book and book.display and book.display != file_name else file_name

    # ================= выделение: подчёркивание и конспект =================
    def _find_item(self, key: str):
        for it in self.outcome.results:
            if f"{it['book']}:{it['rowid']}" == key:
                return it
        return None

    def card_dict(self, key: str):
        """Свежая карточка одного показанного топика (для точечного обновления в родном рендерере)."""
        for i, it in enumerate(self.outcome.results):
            if f"{it['book']}:{it['rowid']}" == key and it.get('content') is not None:
                brief = self.config.get_bool('brief_view', False) and not self.reading
                expanded = key in self.expanded
                return self._card(i, key, it, brief and not expanded, expanded)
        return None

    def _segments(self, key: str, item: dict):
        brief = self.config.get_bool('brief_view', False) and key not in self.expanded and not self.reading
        return self._view(key, item, brief)[2]

    def _content_pieces(self, key: str, ds: int, de: int):
        item = self._find_item(key)
        if item is None:
            return None, []
        return item, marks_mod.display_span_to_content(self._segments(key, item), ds, de)

    def _layers(self, item: dict, segments) -> dict:
        """Подчёркнутое, «+Шрифт», «+Цвет» и свои мысли одной карточки — в координатах её текста на экране."""
        content, fkey = item['content'], marks_mod.file_key(item['book'])

        def spans(records):
            out = []
            for _m, cs, ce in marks_mod.find_mark_spans(content, records):
                out.extend(marks_mod.content_span_to_display(segments, cs, ce))
            return sorted(out)
        notes = []
        for nt, cs, ce in marks_mod.find_mark_spans(content, self.marks.notes_for_file(fkey)):
            shown = marks_mod.content_span_to_display(segments, cs, ce)
            if shown:
                notes.append((shown[-1][1], nt.text or '', nt.flags or '', nt.id))
        return {'marks': spans(self.marks.marks_for_file(fkey)),
                'big': spans(self.marks.styles_for_file(fkey, 'big')),
                'color': spans(self.marks.styles_for_file(fkey, 'color')),
                'notes': sorted(notes)}

    def card_layers(self, key: str) -> dict:
        """Слои одной карточки — чтобы обновить только её, а не весь список."""
        item = self._find_item(key)
        if item is None:
            return {'marks': [], 'big': [], 'color': [], 'notes': []}
        return self._layers(item, self._segments(key, item))

    def card_marks(self, key: str):
        return self.card_layers(key)['marks']

    def font_step(self) -> int:
        return max(1, self.config.get_int('font_step', 2))

    def selection_marked(self, key: str, ds: int, de: int) -> bool:
        """Выделенное уже целиком подчёркнуто?"""
        item, pieces = self._content_pieces(key, ds, de)
        if item is None or not pieces:
            return False
        located = marks_mod.find_mark_spans(item['content'], self.marks.marks_for_file(marks_mod.file_key(item['book'])))
        for cs, ce in pieces:
            info = marks_mod.analyze_span(item['content'], located, cs, ce)
            if info is None or not info[3]:
                return False
        return True

    def mark_selection(self, key: str, ds: int, de: int, publish: bool = True) -> bool:
        """Выделенное автоматически попадает в подчёркнутое (как «Ч» на ПК). True — что-то добавлено."""
        item, pieces = self._content_pieces(key, ds, de)
        if item is None:
            return False
        content, file_name, changed = item['content'], item['book'], False
        located_all = marks_mod.find_mark_spans(content, self.marks.marks_for_file(marks_mod.file_key(file_name)))
        for cs, ce in pieces:
            info = marks_mod.analyze_span(content, located_all, cs, ce)
            if info is None:
                continue
            cs2, ce2, overlapping, fully = info
            if fully:
                continue
            delete_ids, spans = marks_mod.plan_add(cs2, ce2, overlapping)
            self.marks.delete_marks(delete_ids)
            for s_, e_ in spans:
                fields = marks_mod.span_fields(content, s_, e_)
                if fields:
                    self.marks.add_mark(file_name, *fields)
                    changed = True
            located_all = marks_mod.find_mark_spans(content, self.marks.marks_for_file(marks_mod.file_key(file_name)))
        if changed and publish:
            self._publish()
        return changed

    def unmark_selection(self, key: str, ds: int, de: int, publish: bool = True) -> bool:
        item, pieces = self._content_pieces(key, ds, de)
        if item is None:
            return False
        content, file_name, changed = item['content'], item['book'], False
        for cs, ce in pieces:
            located = marks_mod.find_mark_spans(content, self.marks.marks_for_file(marks_mod.file_key(file_name)))
            info = marks_mod.analyze_span(content, located, cs, ce)
            if info is None or not info[2]:
                continue
            cs2, ce2, overlapping, _fully = info
            delete_ids, keep = marks_mod.plan_remove(content, cs2, ce2, overlapping)
            self.marks.delete_marks(delete_ids)
            for s_, e_ in keep:
                fields = marks_mod.span_fields(content, s_, e_)
                if fields:
                    self.marks.add_mark(file_name, *fields)
            self._subtract_styles(item, cs2, ce2)            # размер и цвет живут только внутри подчёркнутого
            changed = True
        if changed and publish:
            self._publish()
        return changed

    def _subtract_styles(self, item: dict, cs: int, ce: int) -> None:
        content, file_name = item['content'], item['book']
        for kind in marks_mod.STYLE_KINDS:
            located = marks_mod.find_mark_spans(content, self.marks.styles_for_file(marks_mod.file_key(file_name), kind))
            info = marks_mod.analyze_span(content, located, cs, ce)
            if info is None or not info[2]:
                continue
            delete_ids, keep = marks_mod.plan_remove(content, info[0], info[1], info[2])
            self.marks.delete_styles(delete_ids)
            for s_, e_ in keep:
                fields = marks_mod.span_fields(content, s_, e_)
                if fields:
                    self.marks.add_style(file_name, kind, *fields)

    # ---------- «+Шрифт» и «+Цвет»: только внутри подчёркнутого ----------
    def _push_undo(self, key: str, item: Optional[dict], what: str, snap=None, conspect_size=None) -> None:
        if item is None or snap is None:
            return
        self.undo_stack.append({'key': key, 'file_name': item['book'], 'snap': snap, 'what': what,
                                'conspect_size': conspect_size})
        del self.undo_stack[:-self.UNDO_DEPTH]
        self.on_undo(len(self.undo_stack))

    UNDO_DEPTH = 7             # сколько последних действий можно откатить кнопкой «Отмена»

    def style_selection(self, kind: str, key: str, ds: int, de: int) -> Optional[str]:
        """Выделенное внутри подчёркнутого получает «+Шрифт» (kind='big') или «+Цвет' ('color'); повторно — снимается.
           Возвращает 'on' / 'off' / 'need_mark' (выделено не внутри подчёркнутого) / None."""
        item, pieces = self._content_pieces(key, ds, de)
        if item is None or not pieces or self.marks_only():
            return None
        content, file_name = item['content'], item['book']
        fkey = marks_mod.file_key(file_name)
        located_marks = marks_mod.find_mark_spans(content, self.marks.marks_for_file(fkey))
        spans = []
        for cs, ce in pieces:
            info = marks_mod.analyze_span(content, located_marks, cs, ce)
            if info is None:
                continue
            if not info[3]:
                return 'need_mark'
            spans.append((info[0], info[1]))
        if not spans:
            return None
        snap = self.marks.snapshot_file(file_name)
        located = marks_mod.find_mark_spans(content, self.marks.styles_for_file(fkey, kind))
        infos = [marks_mod.analyze_span(content, located, cs, ce) for cs, ce in spans]
        if all(i is not None and i[3] for i in infos):                    # всё уже оформлено — снимаем
            for cs, ce in spans:
                located = marks_mod.find_mark_spans(content, self.marks.styles_for_file(fkey, kind))
                info = marks_mod.analyze_span(content, located, cs, ce)
                delete_ids, keep = marks_mod.plan_remove(content, info[0], info[1], info[2])
                self.marks.delete_styles(delete_ids)
                for s_, e_ in keep:
                    fields = marks_mod.span_fields(content, s_, e_)
                    if fields:
                        self.marks.add_style(file_name, kind, *fields)
            result = 'off'
        else:
            for cs, ce in spans:
                located = marks_mod.find_mark_spans(content, self.marks.styles_for_file(fkey, kind))
                info = marks_mod.analyze_span(content, located, cs, ce)
                if info is not None and info[3]:
                    continue
                delete_ids, new = marks_mod.plan_add(info[0], info[1], info[2])
                self.marks.delete_styles(delete_ids)
                for s_, e_ in new:
                    fields = marks_mod.span_fields(content, s_, e_)
                    if fields:
                        self.marks.add_style(file_name, kind, *fields)
            result = 'on'
        self._push_undo(key, item, kind, snap)
        return result

    # ---------- свои мысли ----------
    def add_note(self, key: str, ds: int, de: int, text: str) -> Optional[int]:
        """Мысль привязывается к выделенному слову (фрагменту) и показывается курсивом в скобках за ним."""
        text = (text or '').strip()
        item, pieces = self._content_pieces(key, ds, de)
        if item is None or not pieces or not text or self.marks_only():
            return None
        cs, ce = pieces[-1]
        fields = marks_mod.span_fields(item['content'], cs, ce)
        if not fields:
            return None
        snap = self.marks.snapshot_file(item['book'])
        nid = self.marks.add_note(item['book'], fields[0], fields[1], fields[2], text)
        self._push_undo(key, item, 'note', snap)
        return nid

    def note_info(self, nid: int):
        return self.marks.get_note(nid)

    def _note_edit(self, nid: int, what: str, fn) -> Optional[str]:
        note = self.marks.get_note(nid)
        if note is None:
            return None
        item = self._item_for_file_key(note.file_key)
        snap = self.marks.snapshot_file(note.file_label or note.file_key)
        fn(note)
        key = self._key_for_note(note)
        if item is not None or key:
            self.undo_stack.append({'key': key, 'file_name': note.file_label or note.file_key, 'snap': snap,
                                    'what': what, 'conspect_size': None})
            del self.undo_stack[:-self.UNDO_DEPTH]
            self.on_undo(len(self.undo_stack))
        return key

    def _item_for_file_key(self, fkey: str):
        for it in self.outcome.results:
            if marks_mod.file_key(it['book']) == fkey and it.get('content') is not None:
                return it
        return None

    def _key_for_note(self, note) -> Optional[str]:
        """Карточка, на которой сейчас показана эта мысль (чтобы перерисовать только её)."""
        for it in self.outcome.results:
            if marks_mod.file_key(it['book']) != note.file_key or it.get('content') is None:
                continue
            if any(nt.id == note.id for nt, _s, _e in marks_mod.find_mark_spans(it['content'], [note])):
                return f"{it['book']}:{it['rowid']}"
        return None

    def edit_note_text(self, nid: int, text: str) -> Optional[str]:
        text = (text or '').strip()
        if not text:
            return self.delete_note(nid)
        return self._note_edit(nid, 'note', lambda n: self.marks.update_note(nid, text=text))

    def toggle_note_flag(self, nid: int, flag: str) -> Optional[str]:
        def go(n):
            flags = n.flags or ''
            self.marks.update_note(nid, flags=flags.replace(flag, '') if flag in flags else flags + flag)
        return self._note_edit(nid, 'note', go)

    def delete_note(self, nid: int) -> Optional[str]:
        return self._note_edit(nid, 'note', lambda n: self.marks.delete_note(nid))

    def toggle_mark(self, key: str, ds: int, de: int, text: str) -> Optional[str]:
        """Выделение без подтверждений: не подчёркнуто — подчёркиваем и дублируем в конспект;
           уже подчёркнуто — подчёркивание снимается (конспект не трогаем). Возвращает 'marked' / 'unmarked' / None."""
        item = self._find_item(key)
        if self.marks_only():
            return None
        snap = self.marks.snapshot_file(item['book']) if item else None
        if self.selection_marked(key, ds, de):
            if not self.unmark_selection(key, ds, de, publish=False):
                return None
            self._push_undo(key, item, 'unmark', snap)
            return 'unmarked'
        size = os.path.getsize(self.paths.conspect_path) if os.path.exists(self.paths.conspect_path) else 0
        if not self.mark_selection(key, ds, de, publish=False):
            return None
        self.quote_to_conspect(key, text)
        self._push_undo(key, item, 'mark', snap, size)
        return 'marked'

    UNDO_TEXT = {'mark': "Подчёркивание отменено, запись в конспекте удалена", 'unmark': "Снятие подчёркивания отменено",
                 'big': "Увеличение шрифта отменено", 'color': "Цветовое выделение отменено",
                 'note': "Действие с мыслью отменено"}

    def undo_last_mark(self) -> Optional[str]:
        """Отмена последнего действия (подчёркивание, снятие, шрифт, цвет, мысль; до UNDO_DEPTH штук подряд).
           Возвращает ключ карточки."""
        if not self.undo_stack:
            return None
        e = self.undo_stack.pop()
        try:
            self.marks.restore_file(e['file_name'], e['snap'])
            path = self.paths.conspect_path
            if e.get('conspect_size') is not None and os.path.exists(path) and os.path.getsize(path) > e['conspect_size']:
                with open(path, 'r+b') as f:
                    f.truncate(e['conspect_size'])
            self.on_status(self.UNDO_TEXT.get(e.get('what'), "Действие отменено"))
        except Exception as ex:
            self.on_status(f"Не удалось отменить: {ex}")
        self.on_undo(len(self.undo_stack))
        return e['key']

    # ================= пароль =================
    @staticmethod
    def _hash(password: str, salt: str) -> str:
        import hashlib
        return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), 100000).hex()

    def has_password(self) -> bool:
        return bool(self.config.get('pwd_hash', ''))

    def set_password(self, password: str) -> None:
        if not password:
            self.set_setting('pwd_hash', '')
            self.set_setting('pwd_salt', '')
            return
        salt = os.urandom(16).hex()
        self.set_setting('pwd_salt', salt)
        self.set_setting('pwd_hash', self._hash(password, salt))

    def check_password(self, password: str) -> bool:
        if not self.has_password():
            return True
        return self._hash(password, self.config.get('pwd_salt', '')) == self.config.get('pwd_hash', '')

    def quote_to_conspect(self, key: str, text: str) -> None:
        """«В конспект»: цитата + источник — в файл 0_Конспект.txt (формат как на ПК)."""
        item = self._find_item(key)
        source = self._source_label(item['book']) if item else ''
        try:
            notes.append_quote(self.paths.conspect_path, text, source)
            self.on_status("Сохранено в конспект")
        except Exception as e:
            self.on_status(f"Не удалось записать в конспект: {e}")

    # ================= экспорт, «поделиться», «Дополнить» =================
    def export_marks(self, fmt: str = 'txt') -> Optional[str]:
        """Файл со всеми подчёркнутыми фрагментами в exports/. None — подчёркиваний ещё нет."""
        marks = self.marks.all_marks()
        if not marks:
            self.on_status("Подчёркнутых фрагментов пока нет")
            return None
        path = self._export_name('Подчёркнутое', fmt)
        try:
            (marks_mod.export_marks_docx if fmt == 'docx' else marks_mod.export_marks_txt)(marks, path)
        except Exception as e:
            self.on_status(f"Не удалось сохранить: {e}")
            return None
        self.on_status(f"Сохранено: {path}")
        return path

    def export_notes(self) -> Optional[str]:
        """Файл «Мысли_…txt» со всеми своими мыслями. None — мыслей ещё нет."""
        notes_ = self.marks.all_notes()
        if not notes_:
            self.on_status("Своих мыслей пока нет")
            return None
        path = self._export_name('Мысли', 'txt')
        try:
            marks_mod.export_notes_txt(notes_, path)
        except Exception as e:
            self.on_status(f"Не удалось сохранить: {e}")
            return None
        self.on_status(f"Сохранено: {os.path.basename(path)}")
        return path

    def _export_name(self, label: str, ext: str) -> str:
        """Имя вида «Конспект_2026-11-08_12-30-15.txt»: пункт меню + дата-время экспорта."""
        return os.path.join(self.paths.exports_dir, f"{label}_{time.strftime('%Y-%m-%d_%H-%M-%S')}.{ext}")

    def export_conspect(self) -> Optional[str]:
        return self._export_copy(self.conspect_file(), 'Конспект')

    def export_diary(self) -> Optional[str]:
        return self._export_copy(self.diary_file(), 'Дневник')

    def _export_copy(self, src: Optional[str], label: str) -> Optional[str]:
        if not src:
            return None
        dst = self._export_name(label, 'txt')
        try:
            shutil.copyfile(src, dst)
        except Exception as e:
            self.on_status(f"Не удалось сохранить: {e}")
            return None
        self.on_status(f"Сохранено: {os.path.basename(dst)}")
        return dst

    def list_exports(self) -> list:
        """Экспортированные файлы: [(имя, путь, время, размер)], последние сверху."""
        out = []
        try:
            for n in os.listdir(self.paths.exports_dir):
                p = os.path.join(self.paths.exports_dir, n)
                if os.path.isfile(p):
                    st = os.stat(p)
                    out.append((n, p, st.st_mtime, st.st_size))
        except OSError:
            pass
        out.sort(key=lambda x: x[2], reverse=True)
        return out

    def conspect_file(self) -> Optional[str]:
        if os.path.exists(self.paths.conspect_path) and os.path.getsize(self.paths.conspect_path) > 0:
            return self.paths.conspect_path
        self.on_status("Конспект пока пуст")
        return None

    def append_target(self) -> str:
        """Книга для «Дополнить»: запомненная, если она ещё есть в списке."""
        name = self.config.get('append_target', '')
        names = [b.file_name for b in self.library.books]
        return name if name in names else ''

    def add_to_diary(self, text: str) -> bool:
        """«В дневник»: текст дописывается в dnev.txt (папка книг), книга переиндексируется и попадает в поиск."""
        if not text.strip():
            return False
        path = self.paths.diary_path
        try:
            if not os.path.exists(path):
                open(path, 'wb').close()
            notes.append_to_book(path, text)
        except Exception as e:
            self.on_status(f"Не удалось записать в дневник: {e}")
            return False
        self.library.scan()
        self.publish_books()
        self.on_status("Записано в дневник.")
        self.maybe_index([os.path.basename(path)])
        return True

    def diary_file(self) -> Optional[str]:
        path = self.paths.diary_path
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return path
        self.on_status("Дневник пока пуст")
        return None

    def last_block(self, file_name: str, max_chars: int = 700) -> str:
        """Последний топик книги (по её разделителю) — показывается над полем ввода в «Дополнить»."""
        book = self.library.find(file_name)
        if book is None:
            return ''
        try:
            sections = split_into_sections_by_separator(read_text_file(book.path).splitlines(), book.separator)
        except Exception as e:
            return f"(не удалось прочитать: {e})"
        if not sections:
            return ''
        content = '\n'.join(sections[-1]['lines']).strip()
        return ('… ' + content[-max_chars:]) if len(content) > max_chars else content

    def diary_tail(self, max_chars: int = 3000) -> str:
        """Конец дневника (последние записи) — показывается в верхней части окна «Дневник»."""
        path = self.paths.diary_path
        if not os.path.exists(path):
            return ''
        try:
            text = read_text_file(path).rstrip()
        except Exception as e:
            return f"(не удалось прочитать: {e})"
        return ('… ' + text[-max_chars:]) if len(text) > max_chars else text

    def append_to_book(self, file_name: str, text: str) -> bool:
        book = self.library.find(file_name)
        if book is None:
            self.on_status("Книга не найдена")
            return False
        try:
            notes.append_to_book(book.path, text)
        except Exception as e:
            self.on_status(f"Не удалось дополнить: {e}")
            return False
        self.config.set('append_target', file_name)       # выбор запоминается
        self.config.save()
        self.library.scan()
        self.publish_books()
        self.on_status(f"Добавлено в «{book.display}».")
        self.maybe_index([file_name])
        return True

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
            if not self.outcome.results:
                self.selection_status()
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
        self.on_status(f"Выбрана книга {book.display}, страница {base} из {total}")

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
            self.on_status(f"Выбрана книга {book.display}, страница {item['ordinal']} из {self.reading['total']}")
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

    # ================= синхронизация с папкой (Google Диск) =================
    def sync_uri(self) -> str:
        return self.config.get('sync_uri', '')

    def sync_now(self, folder=None) -> None:
        """Ручная синхронизация подчёркиваний, конспекта и дневника с папкой. folder — для тестов (DirFolder)."""
        if folder is None:
            uri = self.sync_uri()
            if not uri:
                self.on_status("Сначала выберите папку синхронизации")
                return
            try:
                from .ui.safsync import SafFolder
                folder = SafFolder(uri)
            except Exception as e:
                self.on_status(f"Папка синхронизации недоступна: {e}")
                if self.on_sync:
                    self.on_sync('error', f"Папка синхронизации недоступна:\n{e}")
                return
        if getattr(self, '_syncing', False):
            return
        self._syncing = True
        self.on_status("Синхронизация…")
        if self.on_sync:
            self.on_sync('start', "Идёт синхронизация…\nПодождите, не закрывайте приложение.")
        files = {'0_Конспект.txt': self.paths.conspect_path, 'dnev.txt': self.paths.diary_path}

        def work():
            try:
                rep = syncmerge.sync_all(folder, self.marks, files, os.path.join(self.paths.root, 'sync_base'))
                err = None
            except Exception as e:
                rep, err = None, e

            def done():
                self._syncing = False
                if err is not None:
                    self.on_status(f"Синхронизация не удалась: {err}")
                    if self.on_sync:
                        self.on_sync('error', f"Синхронизация не удалась:\n{err}")
                    return
                if rep['marks_added'] or rep['marks_removed']:
                    self.reset_views()                          # карточки на экране устарели
                if rep['texts_changed']:
                    self.library.scan()
                    self.publish_books()
                    if 'dnev.txt' in rep['texts_changed']:
                        self.maybe_index(['dnev.txt'])
                msg = (f"Синхронизация завершена.\nПодчёркиваний: +{rep['marks_added']}, −{rep['marks_removed']}\n"
                       f"Файлов обновлено на телефоне: {len(rep['texts_changed'])}, в папке: {len(rep['texts_pushed'])}")
                self.on_status(msg.replace('\n', ' · '))
                if self.on_sync:
                    self.on_sync('done', msg)
            self.ui_post(done)
        self._run(work)

    # ---------- архив папки синхронизации (аварийное восстановление) ----------
    save_download = None            # Android: функция, кладущая файл в системную папку «Загрузки»; по умолчанию ~/Downloads

    def _sync_folder(self, folder=None):
        if folder is not None:
            return folder
        uri = self.sync_uri()
        if not uri:
            raise RuntimeError("Сначала выберите папку синхронизации")
        from .ui.safsync import SafFolder
        return SafFolder(uri)

    def backup_sync_folder(self, cb=None, folder=None) -> None:
        """Все файлы папки синхронизации -> один zip в «Загрузках» (не удаляется при переустановке приложения).
           cb(ok, текст) вызывается в потоке интерфейса."""
        cb = cb or (lambda ok, text: None)
        try:
            folder = self._sync_folder(folder)
        except Exception as e:
            cb(False, f"Архив не создан:\n{e}")
            return

        def work():
            import zipfile
            tmp, ok, msg = None, False, ''
            try:
                names = folder.names()
                if not names:
                    raise RuntimeError("в папке синхронизации нет файлов")
                os.makedirs(self.paths.exports_dir, exist_ok=True)
                tmp = os.path.join(self.paths.exports_dir, f"Синева_папка_синхронизации_{time.strftime('%Y-%m-%d_%H-%M-%S')}.zip")
                count = 0
                with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
                    for n in names:
                        data = folder.read(n)
                        if data is not None:
                            z.writestr(n, data)
                            count += 1
                saver = self.save_download or self._save_download_default
                shown = saver(tmp)
                ok, msg = True, f"Архив готов: {shown}\nФайлов в архиве: {count}"
            except Exception as e:
                msg = f"Архив не создан:\n{e}"
            finally:
                if tmp and os.path.exists(tmp):
                    try:
                        os.remove(tmp)                      # копия осталась в «Загрузках»
                    except OSError:
                        pass
            self.ui_post(lambda: cb(ok, msg))
        self._run(work)

    def _save_download_default(self, path: str) -> str:
        import shutil
        d = os.path.join(os.path.expanduser('~'), 'Downloads')
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, os.path.basename(path))
        shutil.copyfile(path, dst)
        return dst

    # ================= служебное =================
    def _run(self, fn: Callable) -> None:
        if self.run_async:
            threading.Thread(target=fn, daemon=True).start()
        else:
            fn()
