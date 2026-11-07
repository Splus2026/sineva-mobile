"""Подсветка совпадений: интервалы и краткий вид топика (из Синевы+, utils/highlighting.py), плюс разметка Kivy."""
import re

"""Чистые (без обращения к Tkinter) функции для подсветки совпадений и
определения позиций в тексте. Вынесены из services/search_controller.py при
выделении отрисовки результатов в ui/results_renderer.py — эти функции
одинаково нужны и контроллеру (для передачи данных в отрисовщик), и самому
отрисовщику, поэтому им место в общем модуле utils/, а не в UI-модуле."""


def get_intervals(text, exact_words, partial_fragments):
    """Находит и объединяет интервалы точных и частичных совпадений слов
       запроса в тексте. Возвращает список [start, end, prio] (prio=2 —
       точное совпадение, prio=1 — частичное), отсортированный по позиции."""
    intervals = []
    for w in exact_words:
        pattern = re.compile(r'\b' + re.escape(w) + r'\b', re.IGNORECASE)
        for match in pattern.finditer(text):
            intervals.append((match.start(), match.end(), 2))
    for frag in partial_fragments:
        pattern = re.compile(re.escape(frag), re.IGNORECASE)
        for match in pattern.finditer(text):
            intervals.append((match.start(), match.end(), 1))
    if not intervals:
        return []
    intervals.sort(key=lambda x: (x[0], x[1]))
    merged = []
    for start, end, prio in intervals:
        if not merged or start > merged[-1][1]:
            merged.append([start, end, prio])
        else:
            if start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
                merged[-1][2] = max(merged[-1][2], prio)
    return merged


def build_brief_view_mapped(content, exact_words, partial_fragments):
    """Строит краткую версию текста топика: оставляет только предложения,
       в которых есть подсветка слов запроса, а пропущенный между ними текст
       заменяет многоточием. Текст до первого и после последнего такого
       предложения не показывается вовсе. Возвращает (текст, интервалы) —
       интервалы подсветки пересчитаны под координаты нового (короткого)
       текста, чтобы их можно было применить так же, как обычно.

       Третий элемент результата — карта кусков segments:
       [(начало_на_экране, начало_в_исходном_тексте, длина)]. Короткий текст
       состоит из непрерывных кусков исходного, склеенных " … ", и по этой
       карте выделение на экране переводится в смещения исходного текста (и
       обратно) — так подчёркивание ("Ч", services/marks.py) работает и в
       кратком режиме. Если текст показан целиком, карта — один кусок."""
    intervals = get_intervals(content, exact_words, partial_fragments)
    identity = [(0, 0, len(content))]
    if not intervals:
        return content, intervals, identity

    # Разбиваем текст топика на предложения по конечной пунктуации
    sentence_bounds = []
    pos = 0
    for m in re.finditer(r'[^.!?]*[.!?]+', content):
        sentence_bounds.append((pos, m.end()))
        pos = m.end()
    if pos < len(content):
        sentence_bounds.append((pos, len(content)))
    if not sentence_bounds:
        return content, intervals, identity

    kept_flags = [
        any(istart < e and iend > s for istart, iend, _ in intervals)
        for s, e in sentence_bounds
    ]
    if not any(kept_flags):
        return content, intervals, identity

    # Группируем подряд идущие "оставляемые" предложения в непрерывные
    # диапазоны исходного текста
    runs = []
    i = 0
    n = len(sentence_bounds)
    while i < n:
        if kept_flags[i]:
            run_start = sentence_bounds[i][0]
            j = i
            while j < n and kept_flags[j]:
                j += 1
            run_end = sentence_bounds[j - 1][1]
            runs.append((run_start, run_end))
            i = j
        else:
            i += 1

    brief_parts = []
    brief_intervals = []
    segments = []
    cursor = 0
    for run_idx, (r_start, r_end) in enumerate(runs):
        if run_idx > 0:
            sep = " … "
            brief_parts.append(sep)
            cursor += len(sep)
        run_text = content[r_start:r_end]
        segments.append((cursor, r_start, len(run_text)))
        brief_parts.append(run_text)
        for istart, iend, prio in intervals:
            if istart >= r_start and iend <= r_end:
                brief_intervals.append((cursor + (istart - r_start), cursor + (iend - r_start), prio))
        cursor += len(run_text)

    return "".join(brief_parts), brief_intervals, segments


def build_brief_view(content, exact_words, partial_fragments):
    """Краткая версия текста топика — (текст, интервалы подсветки). Карту кусков
       для перевода выделения на экране в исходный текст даёт
       build_brief_view_mapped (это та же функция, здесь карта отбрасывается)."""
    text, intervals, _segments = build_brief_view_mapped(content, exact_words, partial_fragments)
    return text, intervals
