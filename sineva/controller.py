"""Логика экранов без Kivy: состояние поиска, выбор книг, индексация, страницы результатов.

Интерфейс (Kivy) только подписывается на колбэки on_* и вызывает методы. Благодаря этому всё поведение
проверяется тестами на ПК. Колбэки вызываются через ui_post — в Kivy это Clock.schedule_once, в тестах — вызов на месте.
"""
import os
import threading
import time
from typing import Callable, Dict, List, Optional

from . import marks as marks_mod
from . import notes
from .config import AppConfig
from .highlight import build_brief_view_mapped, get_intervals
from .library import Book, Library
from .paths import AppPaths
from .search import SearchOutcome, SearchService
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
        self.expanded = set()                   # ключи раскрытых (полный текст) топиков
        self.selected = self._load_selected()
        self._token = 0
        self._timer = None

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
        books = [self.library.find(n) for n in file_names]
        books = [b for b in books if b]
        if not books:
            return

        def work():
            for book in books:
                try:
                    self.ui_post(lambda b=book: self.on_status(f"Индексация: {b.display}…"))
                    self.library.index_book(book, lambda d, t, b=book: self.ui_post(
                        lambda: self.on_progress(b.display, d, t)))
                except Exception as e:
                    self.ui_post(lambda b=book, e=e: self.on_status(f"Ошибка индексации {b.display}: {e}"))
            def done():
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
        self._token += 1
        token = self._token
        books = self.selected_books()
        if not books:
            self.on_status("Выберите книги на вкладке «Книги»")
            return

        def work():
            outcome = self.search.search(text, books, self.mode, cancelled=lambda: token != self._token)
            if token == self._token:
                self.ui_post(lambda: self._apply(outcome, token))
        self._run(work)

    def _apply(self, outcome: SearchOutcome, token: int) -> None:
        if token != self._token:
            return
        self.outcome = outcome
        self.shown = self.config.get_int('page_size', 20)
        self.expanded.clear()
        self._publish()
        visible = len(self.search.visible(outcome))
        if outcome.errors and not outcome.results:
            self.on_status('; '.join(outcome.errors))
        else:
            self.on_status(f"Найдено топиков: {visible} · {outcome.elapsed_ms:.0f} мс"
                           + (f" · {'; '.join(outcome.errors)}" if outcome.errors else ''))

    # ================= результаты =================
    def _publish(self) -> None:
        visible = self.search.visible(self.outcome)
        brief = self.config.get_bool('brief_view', False)
        cards = []
        for i, item in enumerate(visible[:self.shown]):
            key = f"{item['book']}:{item['rowid']}"
            expanded = key in self.expanded
            cards.append(self._card(i, key, item, brief and not expanded, expanded))
        self.on_cards(cards, len(visible), len(visible) > self.shown)
        self.on_words(self.search.panel_words(self.outcome))

    def _card(self, i: int, key: str, item: dict, brief: bool, expanded: bool) -> dict:
        content = item['content']
        exact, frags = self.search.highlight_spec(self.outcome, item)
        if brief:
            text, hl, segments = build_brief_view_mapped(content, exact, frags)
        else:
            text, hl, segments = content, get_intervals(content, exact, frags), [(0, 0, len(content))]
        spans = []
        for _m, cs, ce in marks_mod.find_mark_spans(content, self.marks.marks_for_file(marks_mod.file_key(item['book']))):
            spans.extend(marks_mod.content_span_to_display(segments, cs, ce))
        return {'key': key, 'book': item['book'], 'header': self.search.header_markup(item, i + 1),
                'text': text, 'hl': [tuple(x) for x in hl], 'marks': sorted(spans), 'segments': segments,
                'expanded': expanded, 'source': self._source_label(item['book'])}

    def _source_label(self, file_name: str) -> str:
        book = self.library.find(file_name)
        return f"{file_name} ({book.display})" if book and book.display and book.display != file_name else file_name

    # ================= выделение: подчёркивание и конспект =================
    def _find_item(self, key: str):
        for it in self.outcome.results:
            if f"{it['book']}:{it['rowid']}" == key:
                return it
        return None

    def _content_pieces(self, key: str, ds: int, de: int):
        item = self._find_item(key)
        if item is None:
            return None, []
        brief = self.config.get_bool('brief_view', False) and key not in self.expanded
        if brief:
            exact, frags = self.search.highlight_spec(self.outcome, item)
            _t, _h, segments = build_brief_view_mapped(item['content'], exact, frags)
        else:
            segments = [(0, 0, len(item['content']))]
        return item, marks_mod.display_span_to_content(segments, ds, de)

    def mark_selection(self, key: str, ds: int, de: int) -> bool:
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
        if changed:
            self._publish()
        return changed

    def unmark_selection(self, key: str, ds: int, de: int) -> bool:
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
        if changed:
            self._publish()
        return changed

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
        path = os.path.join(self.paths.exports_dir, f"underlines_{time.strftime('%Y-%m-%d_%H%M')}.{fmt}")
        try:
            (marks_mod.export_marks_docx if fmt == 'docx' else marks_mod.export_marks_txt)(marks, path)
        except Exception as e:
            self.on_status(f"Не удалось сохранить: {e}")
            return None
        self.on_status(f"Сохранено: {path}")
        return path

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
        self.on_status("Записано в дневник. Переиндексация…")
        self.index_books([os.path.basename(path)])
        return True

    def diary_file(self) -> Optional[str]:
        path = self.paths.diary_path
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return path
        self.on_status("Дневник пока пуст")
        return None

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
        self.on_status(f"Добавлено в «{book.display}». Переиндексация…")
        self.index_books([file_name])
        return True

    def show_more(self) -> None:
        self.shown += self.config.get_int('page_size', 20)
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
