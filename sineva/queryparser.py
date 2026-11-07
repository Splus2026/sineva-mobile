# utils/query_parser.py
import re
import math
from typing import List, Tuple, Optional

def parse_query_words(query: str, min_word_length: int = 3) -> List[Tuple[str, Optional[int], bool]]:
    """
    Разбирает поисковый запрос.
    Возвращает список кортежей: (слово, минимальная_длина, must_start_with).

    min_word_length: минимальная длина слова, чтобы попасть в результат
    (по умолчанию 3, как и раньше). Используется, в частности, режимом
    разделения окна результатов на два Excel-файла, где ищутся и совпадения
    из двух символов (например, "12" в "Болт М 12") — там вызывающий код
    передаёт min_word_length=2.
    """
    # ёЁ входят в слово (как и в остальных местах проекта: vector_model_manager, облако тегов, ключевые
    # понятия) — иначе «ещё», «всё», «её» превращались в пустой запрос, а «жёлтый» — в «лтый».
    raw_words = re.findall(r'[а-яА-ЯёЁa-zA-Z0-9.]+', query)
    result = []
    for w in raw_words:
        if '.' not in w:
            if w and len(w) >= min_word_length:
                result.append((w, None, False))
            continue

        dot_positions = [i for i, ch in enumerate(w) if ch == '.']
        if not dot_positions:
            if w and len(w) >= min_word_length:
                result.append((w, None, False))
            continue

        must_start_with = dot_positions[0] == 0

        min_len = None
        if len(dot_positions) > 1:
            last_dot_pos = dot_positions[-1]
            prefix_raw = w[:last_dot_pos]
            prefix_clean = prefix_raw.replace('.', '')
            min_len = len(prefix_clean) if prefix_clean else 1
        else:
            if must_start_with:
                min_len = None
            else:
                dot_pos = dot_positions[0]
                prefix_raw = w[:dot_pos]
                prefix_clean = prefix_raw.replace('.', '')
                min_len = len(prefix_clean) if prefix_clean else 1

        full_word = w.replace('.', '')
        if full_word and len(full_word) >= min_word_length:
            result.append((full_word, min_len, must_start_with))
    return result


def get_fragments_with_levels(word: str, min_len_override: Optional[int] = None) -> List[Tuple[str, int]]:
    """
    Генерирует фрагменты слова с уровнями релевантности (5-10).
    Возвращает список (фрагмент, уровень).
    """
    L = len(word)
    if L == 0:
        return []
    if L <= 3:
        # Короткие слова (в т.ч. двусимвольные — см. пояснение у
        # parse_query_words про режим разделения с двуграммами) не
        # разбиваются на более мелкие фрагменты: например, для "12" не нужен
        # вырожденный фрагмент "1", который давал бы ложные частичные
        # совпадения по одной цифре/букве в других топиках.
        return [(word, 10)]
    if min_len_override is not None:
        min_len = max(1, min(min_len_override, L))
    else:
        min_len = max(1, math.ceil(L * 0.5))

    result = []
    for length in range(L, min_len - 1, -1):
        frag = word[:length]
        percent = (length / L) * 100
        if percent >= 95:
            level = 10
        elif percent >= 85:
            level = 9
        elif percent >= 75:
            level = 8
        elif percent >= 65:
            level = 7
        elif percent >= 55:
            level = 6
        else:
            level = 5
        result.append((frag, level))
    return result


def compute_matches(content: str, query_words_info: List[Tuple[str, Optional[int], bool]]) -> dict:
    """
    Вычисляет для текста content:
    - matched_words: set слов, которые совпали
    - counts: словарь {уровень: количество}
    - max_level, count_at_max
    """
    words_in_text = re.findall(r'[а-яА-ЯёЁa-zA-Z0-9]+', content)
    counts = {i: 0 for i in range(5, 11)}
    matched_words = set()

    # Собираем все фрагменты с уровнями и флагами.
    # exact_only: слово было задано "точечным" фильтром — точками с обеих
    # сторон, например ".12." — это означает "без сокращения" (min_len
    # доходит до полной длины слова). Раньше это дополнительно всё равно
    # проверялось через startswith, а не через точное равенство — из-за
    # этого, например, ".12." совпадало не только с "12", но и с "120",
    # "12005" и любым другим числом/словом, начинающимся на "12" (в тексте
    # это редко заметно, а в числовых кодах прайс-листа — сплошь и рядом).
    all_fragments = []
    for word, min_len, must_start in query_words_info:
        exact_only = must_start and min_len is not None and min_len >= len(word)
        for frag, level in get_fragments_with_levels(word, min_len):
            all_fragments.append((frag, level, must_start, exact_only))

    for token in words_in_text:
        token_lower = token.lower()
        best_level = 0
        for frag, level, must_start, exact_only in all_fragments:
            frag_lower = frag.lower()
            if exact_only:
                is_match = (token_lower == frag_lower)
            elif must_start:
                is_match = token_lower.startswith(frag_lower)
            else:
                is_match = frag_lower in token_lower
            if is_match and level > best_level:
                best_level = level
        if best_level > 0:
            counts[best_level] += 1
            matched_words.add(token_lower)

    if sum(counts.values()) == 0:
        return None

    max_level = 0
    for lvl in range(10, 4, -1):
        if counts[lvl] > 0:
            max_level = lvl
            break
    if max_level == 0:
        max_level = 5
        counts[5] = 1
    count_at_max = counts[max_level]

    return {
        'matched_words': list(matched_words),
        'counts': counts,
        'max_level': max_level,
        'count_at_max': count_at_max
    }


def compute_matches_unique(content: str, query_words_info: List[Tuple[str, Optional[int], bool]]) -> dict:
    """Как compute_matches(), но для рейтинга (counts/max_level/count_at_max)
       каждое СЛОВО ЗАПРОСА (не каждое совпавшее в тексте вхождение) даёт
       не больше одного очка за топик — берётся лучший уровень совпадения
       среди всех его вхождений/сокращённых вариантов в этом топике, а не
       сумма по всем вхождениям. Используется в режиме одновременного
       поиска по двум Excel-файлам, где короткие числовые фрагменты запроса
       (двуграммы) иначе легко "накручивают" рейтинг случайными повторами и
       частичными совпадениями внутри одной строки.

       matched_words (для подсветки и панели "Совпадения") считается точно
       так же, как в compute_matches() — по запросу пользователя это
       изменение НЕ должно влиять на подсветку, только на рейтинг."""
    words_in_text = re.findall(r'[а-яА-ЯёЁa-zA-Z0-9]+', content)
    counts = {i: 0 for i in range(5, 11)}
    matched_words = set()

    # Фрагменты группируем по исходному слову запроса — чтобы посчитать
    # лучший уровень ОТДЕЛЬНО для каждого слова, а не суммарно по токенам.
    # exact_only — общее свойство слова целиком (все его фрагменты либо
    # точные, либо нет — get_fragments_with_levels не смешивает их для
    # одного слова), это используется ниже для быстрого пути.
    fragments_by_word = []
    for word, min_len, must_start in query_words_info:
        exact_only = must_start and min_len is not None and min_len >= len(word)
        frags = [(frag, level, must_start, exact_only) for frag, level in get_fragments_with_levels(word, min_len)]
        fragments_by_word.append((word, frags, exact_only))

    best_level_per_word = {}

    # Быстрый путь для "точных" слов (exact_only, см. parse_query_words):
    # множество уникальных токенов текста строится один раз — O(N) — а
    # дальше проверка "есть ли такое слово в тексте" — через множество,
    # O(1) в среднем, вместо полного прохода по всем токенам текста на
    # КАЖДОЕ слово запроса. Для векторного поиска, где слов запроса
    # обычно 15+ и все они точные, а топики могут быть в тысячи слов —
    # это и есть основной оставшийся источник задержки, кратно быстрее.
    token_set_lower = None
    if any(exact_only for _word, _frags, exact_only in fragments_by_word):
        token_set_lower = set(t.lower() for t in words_in_text)

    for word, frags, exact_only in fragments_by_word:
        if exact_only:
            for frag, level, _must_start, _exact_only in frags:
                frag_lower = frag.lower()
                if frag_lower in token_set_lower:
                    matched_words.add(frag_lower)
                    if level > best_level_per_word.get(word, 0):
                        best_level_per_word[word] = level
        else:
            for token in words_in_text:
                token_lower = token.lower()
                for frag, level, must_start, _exact_only in frags:
                    frag_lower = frag.lower()
                    is_match = token_lower.startswith(frag_lower) if must_start else (frag_lower in token_lower)
                    if is_match:
                        matched_words.add(token_lower)
                        if level > best_level_per_word.get(word, 0):
                            best_level_per_word[word] = level

    if not best_level_per_word:
        return None

    for level in best_level_per_word.values():
        counts[level] += 1

    max_level = max(best_level_per_word.values())
    count_at_max = counts[max_level]

    return {
        'matched_words': list(matched_words),
        'counts': counts,
        'max_level': max_level,
        'count_at_max': count_at_max
    }
