"""Поиск по выбранным книгам, панель «Совпадения» (исключение слов) и разметка текста для показа."""
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .highlight import build_brief_view_mapped, get_intervals
from .index import BookIndex
from .library import Book
from .queryparser import get_fragments_with_levels, parse_query_words
from .vector import VectorRegistry, VectorUnavailable

COLOR_EXACT = '0b57d0'     # точное совпадение
COLOR_PART = '1a7f37'      # частичное (фрагмент слова)


def escape_markup(text: str) -> str:
    """Экранирование для разметки Kivy ([b], [color=..] и т.д.)."""
    return text.replace('&', '&amp;').replace('[', '&bl;').replace(']', '&br;')


@dataclass
class SearchOutcome:
    query: str = ''
    words_info: list = field(default_factory=list)       # разобранные слова запроса
    fragments: list = field(default_factory=list)        # (фрагмент, уровень) всех слов запроса
    results: List[dict] = field(default_factory=list)    # все топики, отсортированные от лучшего
    word_counts: Dict[str, int] = field(default_factory=dict)   # совпавшее слово -> в скольких топиках
    elapsed_ms: float = 0.0
    index_ms: float = 0.0                                # из них — выборка и сопоставление по индексам
    modes: set = field(default_factory=set)              # 'FTS' / 'перебор' — как реально искали
    errors: List[str] = field(default_factory=list)
    mode: str = 'text'


class SearchService:
    def __init__(self, config, vectors: Optional[VectorRegistry] = None):
        self.config = config
        self.vectors = vectors or VectorRegistry()
        self.excluded: Set[str] = set()

    def fill(self, items) -> None:
        """Подгружает текст и позицию топиков (поиск возвращает только оценки): нужно лишь для показанных."""
        by_book = {}
        for it in items:
            if it.get('content') is None:
                by_book.setdefault(it['book'], []).append(it)
        for name, its in by_book.items():
            book = getattr(self, '_books', {}).get(name)
            if book is None:
                continue
            idx = BookIndex(book.index_path)
            try:
                data = idx.fetch([it['rowid'] for it in its])
            finally:
                idx.close()
            for it in its:
                it['content'], it['position'] = data.get(it['rowid'], ('', '0'))

    # ---------- поиск ----------
    def search(self, query: str, books: List[Book], mode: str = 'text', cancelled=None,
               count_words: bool = True) -> SearchOutcome:
        """cancelled — функция без аргументов: True, если пользователь уже изменил запрос (поиск бросается)."""
        out = SearchOutcome(query=query, mode=mode)
        self._books = {b.file_name: b for b in books}
        t0 = time.time()
        if mode == 'vector':
            return self._search_vector(query, books, out)
        out.words_info = parse_query_words(query, self.config.get_int('min_query_len', 3))
        if not out.words_info:
            return out
        for word, min_len, _ms in out.words_info:
            out.fragments.extend(get_fragments_with_levels(word, min_len))
        limit = self.config.get_int('candidate_limit', 3000)
        for book in books:
            if cancelled and cancelled():
                return out
            if not book.ready:
                out.errors.append(f"{book.display}: книга не проиндексирована")
                continue
            idx = None
            try:
                idx = BookIndex(book.index_path)
                out.modes.add('словарь')
                t_idx = time.time()
                found = idx.search(out.words_info, limit, cancelled)
                out.index_ms += (time.time() - t_idx) * 1000
                for item in found:
                    item['book'] = book.file_name
                    item['book_display'] = book.display
                    out.results.append(item)
            except Exception as e:           # повреждённый индекс одной книги не должен ронять весь поиск
                out.errors.append(f"{book.display}: {e}")
            finally:
                if idx:
                    idx.close()
        # лучший уровень совпадения, затем число совпадений на этом уровне (как в Синеве+)
        out.results.sort(key=lambda it: (it['meta']['max_level'], it['meta']['count_at_max']), reverse=True)
        if count_words:
            out.word_counts = self._count_words(out.results)
        out.elapsed_ms = (time.time() - t0) * 1000
        return out

    def _search_vector(self, query: str, books: List[Book], out: SearchOutcome) -> SearchOutcome:
        try:
            model = self.vectors.require()
            hits = model.search(query, [b.file_name for b in books])
        except VectorUnavailable as e:
            out.errors.append(str(e))
            return out
        # Когда модели появятся: hits -> топики (BookIndex.get_topic) -> out.results в том же формате.
        out.errors.append("Векторный поиск: вывод результатов будет добавлен вместе с первой моделью")
        return out

    @staticmethod
    def _count_words(results: List[dict]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for item in results:
            for w in item['meta']['matched_words']:
                counts[w] = counts.get(w, 0) + 1
        return counts

    # ---------- исключённые слова ----------
    def toggle_word(self, word: str) -> bool:
        """True — слово теперь исключено."""
        if word in self.excluded:
            self.excluded.discard(word)
            return False
        self.excluded.add(word)
        return True

    def set_all_excluded(self, words, excluded: bool) -> None:
        for w in words:
            (self.excluded.add if excluded else self.excluded.discard)(w)

    def visible(self, outcome: SearchOutcome) -> List[dict]:
        """Топик скрывается, только если все его совпавшие слова исключены."""
        if not self.excluded:
            return outcome.results
        return [it for it in outcome.results
                if any(w not in self.excluded for w in it['meta']['matched_words'])]

    # ---------- панель «Совпадения» ----------
    def panel_words(self, outcome: SearchOutcome) -> List[dict]:
        """[{'word', 'count', 'exact', 'excluded', 'part'}]: сначала точные слова запроса, затем остальные —
           по убыванию длины подсвеченной части (как панель «Совпадения» в Синеве+)."""
        query_words = {w.lower() for w, _m, _s in outcome.words_info}
        rows = []
        for word, count in outcome.word_counts.items():
            exact = word in query_words
            part = word if exact else self._best_fragment(word, outcome.fragments)
            rows.append({'word': word, 'count': count, 'exact': exact, 'part': part,
                         'excluded': word in self.excluded})
        rows.sort(key=lambda r: (not r['exact'], -len(r['part']), r['word']))
        return rows

    @staticmethod
    def _best_fragment(word: str, fragments) -> str:
        best, wl = '', word.lower()
        for frag, _lvl in fragments:
            fl = frag.lower()
            if (fl in wl or wl.startswith(fl)) and len(fl) > len(best):
                best = fl
        return best

    # ---------- текст топика ----------
    def highlight_spec(self, outcome: SearchOutcome, item: dict) -> Tuple[List[str], List[str]]:
        query_words = {w.lower() for w, _m, _s in outcome.words_info}
        exact, frags = [], []
        for w in item['meta']['matched_words']:
            if w in self.excluded:
                continue
            if w in query_words:
                exact.append(w)
            else:
                part = self._best_fragment(w, outcome.fragments)
                if part:
                    frags.append(part)
        return exact, frags

    def topic_markup(self, outcome: SearchOutcome, item: dict, brief: bool = True) -> str:
        """Текст топика с подсветкой в разметке Kivy. brief — только предложения с совпадениями."""
        content = item['content']
        exact, frags = self.highlight_spec(outcome, item)
        if brief:
            text, intervals, _seg = build_brief_view_mapped(content, exact, frags)
        else:
            text, intervals = content, get_intervals(content, exact, frags)
        parts, pos = [], 0
        for start, end, prio in intervals:
            parts.append(escape_markup(text[pos:start]))
            color = COLOR_EXACT if prio == 2 else COLOR_PART
            seg = escape_markup(text[start:end])
            parts.append(f"[b][color={color}]{seg}[/color][/b]" if prio == 2 else f"[color={color}]{seg}[/color]")
            pos = end
        parts.append(escape_markup(text[pos:]))
        return ''.join(parts)

    def header_markup(self, item: dict, number: int) -> str:
        meta = item['meta']
        return (f"[b]{number}. {escape_markup(item.get('book_display', ''))}[/b]  "
                f"[size=12sp][color=777777]ур. {meta['max_level']} · совп. {meta['count_at_max']} · "
                f"поз. {escape_markup(str(item.get('position', '')))}[/color][/size]")

    def word_markup(self, row: dict) -> str:
        label = escape_markup(row['word']) + f"  ({row['count']})"
        if row['excluded']:
            return f"[s][color=b3261e]{label}[/color][/s]"
        return f"[b][color={COLOR_EXACT}]{label}[/color][/b]" if row['exact'] else f"[color={COLOR_PART}]{label}[/color]"
