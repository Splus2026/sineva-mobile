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
from .splitter import split_into_sections_by_separator
from .textio import read_text_file
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
        self.cap = 0                           # сколько «лучших» топиков сейчас доступно (растёт кнопкой)
        self.more_label = ''
        self.offsets = {}                       # ключ топика -> с какого символа показывается окно длинного текста
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

    def rescan(self) -> None:
        """Тихо перечитывает папку книг и mconfig.ini: вызывается при открытии вкладки «Книги» и возврате в приложение."""
        self.library.scan()
        self.selected &= {b.file_name for b in self.library.books}
        self.publish_books()

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
        self.cap = self.config.get_int('best_step', 20)
        self.expanded.clear()
        self.offsets.clear()
        self._publish()
        visible = len(self.search.visible(outcome))
        if outcome.errors and not outcome.results:
            self.on_status('; '.join(outcome.errors))
        else:
            mode = '/'.join(sorted(outcome.modes)) or '—'
            self.on_status(f"Найдено топиков: {visible}, лучшие {min(visible, self.cap)} · поиск {outcome.elapsed_ms:.0f} мс "
                           f"(индекс {outcome.index_ms:.0f} мс, {mode})"
                           + (f" · {'; '.join(outcome.errors)}" if outcome.errors else ''))

    # ================= результаты =================
    def _publish(self) -> None:
        visible = self.search.visible(self.outcome)
        brief = self.config.get_bool('brief_view', False)
        limit = min(len(visible), self.cap)                 # «лучшие N»: остальные — по кнопке
        count = min(self.shown, limit)
        cards = []
        for i, item in enumerate(visible[:count]):
            key = f"{item['book']}:{item['rowid']}"
            expanded = key in self.expanded
            cards.append(self._card(i, key, item, brief and not expanded, expanded))
        step = self.config.get_int('best_step', 20)
        if count < limit:
            self.more_label = f"Показать ещё {min(self.config.get_int('page_size', 3), limit - count)}"
        elif len(visible) > limit:
            self.more_label = f"Ещё {min(step, len(visible) - limit)} лучших (всего {len(visible)})"
        else:
            self.more_label = ''
        self.on_cards(cards, len(visible), bool(self.more_label))
        if self.on_words is not _noop:                 # панель слов отключена в интерфейсе — зря не считаем
            self.on_words(self.search.panel_words(self.outcome))

    MAX_CHARS = 2500          # длиннее — показываем окном: огромная текстура на телефоне даёт чёрные прямоугольники и тормоза

    def _view(self, key: str, item: dict, brief: bool):
        """(текст на экране, интервалы подсветки, карта кусков, постраничность) для топика."""
        content = item['content']
        exact, frags = self.search.highlight_spec(self.outcome, item)
        if brief:
            text, hl, segments = build_brief_view_mapped(content, exact, frags)
            return text, hl, segments, None
        hl = get_intervals(content, exact, frags)
        n = len(content)
        if n <= self.MAX_CHARS:
            return content, hl, [(0, 0, n)], None
        start = self.offsets.get(key)
        if start is None:                                   # сначала — окно вокруг первого совпадения
            start = max(0, (hl[0][0] if hl else 0) - 300)
            start = self._snap(content, start)
            self.offsets[key] = start
        end = min(n, start + self.MAX_CHARS)
        if end < n:
            cut = max(content.rfind('\n', start, end), content.rfind(' ', start, end))
            if cut > start + self.MAX_CHARS // 2:
                end = cut
        prefix = '… ' if start > 0 else ''
        suffix = ' …' if end < n else ''
        shift = len(prefix) - start
        shown = [(max(s_, start) + shift, min(e_, end) + shift, p_) for s_, e_, p_ in hl if e_ > start and s_ < end]
        text = prefix + content[start:end] + suffix
        paging = {'prev': start > 0, 'next': end < n, 'info': f"{start + 1}–{end} из {n} зн."}
        return text, shown, [(len(prefix), start, end - start)], paging

    @staticmethod
    def _snap(content: str, pos: int) -> int:
        """Сдвигает начало окна назад к границе строки или слова, чтобы не рвать слово."""
        if pos <= 0:
            return 0
        cut = max(content.rfind('\n', max(0, pos - 200), pos), content.rfind(' ', max(0, pos - 200), pos))
        return cut + 1 if cut >= 0 else pos

    def shift_card(self, key: str, direction: int) -> None:
        """Длинный топик читается окнами: «Дальше» / «Назад»."""
        item = self._find_item(key)
        if item is None:
            return
        _t, _h, segs, paging = self._view(key, item, False)
        if not paging:
            return
        _d0, c0, length = segs[0]
        if direction > 0:
            self.offsets[key] = self._snap(item['content'], c0 + length - 200)
        else:
            self.offsets[key] = self._snap(item['content'], max(0, c0 - self.MAX_CHARS + 200))
        self._publish()

    def _card(self, i: int, key: str, item: dict, brief: bool, expanded: bool) -> dict:
        content = item['content']
        text, hl, segments, paging = self._view(key, item, brief)
        spans = []
        for _m, cs, ce in marks_mod.find_mark_spans(content, self.marks.marks_for_file(marks_mod.file_key(item['book']))):
            spans.extend(marks_mod.content_span_to_display(segments, cs, ce))
        return {'key': key, 'book': item['book'], 'header': self.search.header_markup(item, i + 1),
                'text': text, 'hl': [tuple(x) for x in hl], 'marks': sorted(spans), 'segments': segments,
                'expanded': expanded, 'paging': paging, 'source': self._source_label(item['book'])}

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
        brief = self.config.get_bool('brief_view', False) and key not in self.expanded
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
        """Сначала дорисовываются карточки внутри лучших N, потом лучшие расширяются на best_step."""
        visible = len(self.search.visible(self.outcome))
        if self.shown >= min(visible, self.cap):
            self.cap += self.config.get_int('best_step', 20)
        self.shown += self.config.get_int('page_size', 3)
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
