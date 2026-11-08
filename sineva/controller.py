"""Логика экранов без Kivy: состояние поиска, выбор книг, индексация, страницы результатов.

Интерфейс (Kivy) только подписывается на колбэки on_* и вызывает методы. Благодаря этому всё поведение
проверяется тестами на ПК. Колбэки вызываются через ui_post — в Kivy это Clock.schedule_once, в тестах — вызов на месте.
"""
import os
import shutil
import threading
import time
from typing import Callable, Dict, List, Optional

from . import marks as marks_mod
from . import notes
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
        self.offsets = {}                       # ключ топика -> с какого символа показывается окно длинного текста
        self.expanded = set()                   # ключи раскрытых (полный текст) топиков
        self.selected = self._load_selected()
        self._token = 0
        self._timer = None
        self.on_reset = lambda: None            # интерфейс очищает карточки обеих вкладок
        self.tab = 'search'                     # активная вкладка с карточками: 'search' | 'read'
        self._states = {}                       # сохранённые состояния неактивной вкладки
        self.dirty = set()                      # вкладки, карточки которых устарели (шрифт, вид) и перерисуются при входе
        self.win_start = 0                      # индекс первой показанной карточки (окно ≤ WINDOW карточек)
        self.pending_append = None              # {'drop': n, 'new': m} — «Показать ещё»: интерфейс дорисовывает, а не перестраивает
        self.reading = None                     # режим чтения: {'book','last','has_next','total'}
        self.on_ask_continue = None             # (название книги, номер блока, продолжить(), с_начала())
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

    def select_all(self, value: bool) -> None:
        self.selected = {b.file_name for b in self.library.books if b.ready} if value else set()
        self._save_selected()
        self.publish_books()

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

    def _apply(self, outcome: SearchOutcome, token: int) -> None:
        if token != self._token:
            return
        self.outcome = outcome
        self.shown = self.config.get_int('page_size', 3)
        self.win_start = 0
        self.expanded.clear()
        self.offsets.clear()
        self._publish()
        visible = len(self.search.visible(outcome))
        if outcome.errors and not outcome.results:
            self.on_status('; '.join(outcome.errors))
        else:
            mode = '/'.join(sorted(outcome.modes)) or '—'
            self.on_status(f"Найдено топиков: {visible} · поиск {outcome.elapsed_ms:.0f} мс "
                           f"(индекс {outcome.index_ms:.0f} мс, {mode})"
                           + (f" · {'; '.join(outcome.errors)}" if outcome.errors else ''))

    # ================= результаты =================
    WINDOW = 20             # сколько карточек одновременно держим на экране и в памяти

    def _items(self):
        return self.outcome.results if self.reading else self.search.visible(self.outcome)

    def _publish(self) -> None:
        self.dirty.discard(self.tab)
        self.dirty.add('read' if self.tab == 'search' else 'search')     # карточки другой вкладки теперь устарели
        items = self._items()
        brief = self.config.get_bool('brief_view', False) and not self.reading
        start = 0 if self.reading else self.win_start
        count = len(items) if self.reading else max(0, min(self.shown, len(items) - start))
        seg = items[start:start + count]
        self.search.fill(seg)                              # текст подгружается только для показанных
        cards = []
        for i, item in enumerate(seg):
            key = f"{item['book']}:{item['rowid']}"
            expanded = key in self.expanded
            cards.append(self._card(start + i, key, item, brief and not expanded, expanded))
        has_more = self.reading['has_next'] if self.reading else (start + count < len(items))
        self.more_label = f"Показать ещё {self.config.get_int('page_size', 3)}" if has_more else ''
        self.on_cards(cards, len(items), has_more)
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
        self.dirty.clear()
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
        if brief:
            text, hl, segments = build_brief_view_mapped(content, exact, frags)
            return text, hl, segments, None
        return content, get_intervals(content, exact, frags), [(0, 0, len(content))], None

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
        spans = []
        for _m, cs, ce in marks_mod.find_mark_spans(content, self.marks.marks_for_file(marks_mod.file_key(item['book']))):
            spans.extend(marks_mod.content_span_to_display(segments, cs, ce))
        header = (f"[b]Блок {item['ordinal']}[/b] [size=12sp][color=777777]из {self.reading['total']} · "
                  f"поз. {escape_markup(str(item.get('position', '')))}[/color][/size]") if self.reading and 'ordinal' in item \
            else self.search.header_markup(item, i + 1)
        return {'key': key, 'book': item['book'], 'header': header,
                'text': text, 'hl': [tuple(x) for x in hl], 'marks': sorted(spans), 'segments': segments,
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

    def _segments(self, key: str, item: dict):
        brief = self.config.get_bool('brief_view', False) and key not in self.expanded and not self.reading
        return self._view(key, item, brief)[2]

    def _content_pieces(self, key: str, ds: int, de: int):
        item = self._find_item(key)
        if item is None:
            return None, []
        return item, marks_mod.display_span_to_content(self._segments(key, item), ds, de)

    def card_marks(self, key: str):
        """Подчёркнутые участки одной карточки в координатах её текста — чтобы обновить только её, а не весь список."""
        item = self._find_item(key)
        if item is None:
            return []
        segments = self._segments(key, item)
        spans = []
        located = marks_mod.find_mark_spans(item['content'], self.marks.marks_for_file(marks_mod.file_key(item['book'])))
        for _m, cs, ce in located:
            spans.extend(marks_mod.content_span_to_display(segments, cs, ce))
        return sorted(spans)

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
            changed = True
        if changed and publish:
            self._publish()
        return changed

    def toggle_mark(self, key: str, ds: int, de: int, text: str) -> Optional[str]:
        """Выделение без подтверждений: не подчёркнуто — подчёркиваем и дублируем в конспект;
           уже подчёркнуто — подчёркивание снимается (конспект не трогаем). Возвращает 'marked' / 'unmarked' / None."""
        if self.selection_marked(key, ds, de):
            return 'unmarked' if self.unmark_selection(key, ds, de, publish=False) else None
        if not self.mark_selection(key, ds, de, publish=False):
            return None
        self.quote_to_conspect(key, text)
        return 'marked'

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
        if number > 1 and self.on_ask_continue:
            self.on_ask_continue(book.display, number,
                                 lambda: self._read_from(book, saved), lambda: self._read_from(book, first))
        elif number > 1:
            self._read_from(book, saved)
        else:
            self._read_from(book, first)

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
        """Начинает чтение с блока rowid=start: показывает page_size блоков подряд."""
        rows, total, has_next, base = self._read_rows(book, start, self.config.get_int('page_size', 3))
        if not rows:
            self.on_status("В книге нет блоков")
            return
        outcome = SearchOutcome(query='')
        outcome.results = self._read_items(book, rows, base)
        self.outcome = outcome
        self.reading = {'book': book.file_name, 'last': rows[-1][0], 'has_next': has_next, 'total': total,
                        'fresh': True}
        self.expanded.clear()
        self.offsets.clear()
        self.set_setting(self._read_key(book.file_name), rows[-1][0])       # запоминается последнее прочитанное место
        self._publish()
        self.on_status(f"Чтение: «{book.display}» · блоки с {base} из {total}")

    def read_next(self) -> None:
        r = self.reading
        book = self.library.find(r['book']) if r else None
        if not book or not r['has_next']:
            return
        step = self.config.get_int('page_size', 3)
        rows, total, has_next, base = self._read_rows(book, r['last'] + 1, step)
        if not rows:
            r['has_next'] = False
            self._publish()
            return
        results = self.outcome.results
        results.extend(self._read_items(book, rows, base))
        drop = max(0, len(results) - self.WINDOW)
        if drop:
            self._release(results[:drop])
            del results[:drop]
        r['last'], r['has_next'] = rows[-1][0], has_next
        self.set_setting(self._read_key(book.file_name), rows[-1][0])
        self.pending_append = {'drop': drop, 'new': len(rows)}
        self._publish()
        self.on_status(f"Чтение: «{book.display}» · блок {results[-1]['ordinal']} из {total}")

    def show_more(self) -> None:
        """«Показать ещё 3»: карточки дорисовываются; сверх WINDOW самые первые выгружаются из памяти."""
        if self.reading:
            self.read_next()
            return
        items = self._items()
        step = self.config.get_int('page_size', 3)
        add = min(step, len(items) - (self.win_start + self.shown))
        if add <= 0:
            return
        drop = max(0, self.shown + add - self.WINDOW)
        if drop:
            self._release(items[self.win_start:self.win_start + drop])
            self.win_start += drop
        self.shown += add - drop
        self.pending_append = {'drop': drop, 'new': add}
        self._publish()

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

    # ================= служебное =================
    def _run(self, fn: Callable) -> None:
        if self.run_async:
            threading.Thread(target=fn, daemon=True).start()
        else:
            fn()
