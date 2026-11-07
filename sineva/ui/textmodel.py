"""Разметка текста топика с цветными подложками: без Kivy, поэтому проверяется тестами.

Подложка в Kivy рисуется прямоугольниками по зонам [ref=…] (в разметке Kivy нет фона у текста).
Поэтому текст режется на «атомы» — отрезки с одинаковым стилем; каждый стилизованный атом оборачивается в [ref=N].
В режиме выделения все слова — отдельные атомы, чтобы по касанию находить слово под пальцем."""
import re
from typing import Dict, List, Optional, Tuple

from ..search import escape_markup

WORD_RE = re.compile(r'\S+\s*')                 # слово вместе с хвостовым пробелом
SENT_END = '.!?…'
MARK_COLOR = '0a2a8c'                           # тёмно-синий шрифт подчёркнутого


def build_markup(text: str, hl, marks, sel: Optional[Tuple[int, int]] = None,
                 select_mode: bool = False) -> Tuple[str, Dict[int, dict]]:
    """hl — [(start, end, prio)], prio 2 — слово запроса, 1 — часть слова; marks — [(start, end)] подчёркнутое;
       sel — выделение пальцем. Возвращает (разметка, {ref_id: {'start','end','hl','mark','sel'}})."""
    n = len(text)
    cuts = {0, n}
    for s, e, _p in hl:
        cuts.update((s, e))
    for s, e in marks:
        cuts.update((s, e))
    if sel:
        cuts.update(sel)
    if select_mode:
        for m in WORD_RE.finditer(text):
            cuts.update((m.start(), m.end()))
            stripped = m.group().rstrip()
            cuts.add(m.start() + len(stripped))          # слово отдельно от пробела
    cuts = sorted(c for c in cuts if 0 <= c <= n)
    parts, refs = [], {}
    for a, b in zip(cuts, cuts[1:]):
        if a == b:
            continue
        seg = text[a:b]
        prio = max([p for s, e, p in hl if s <= a and b <= e], default=0)
        mark = any(s <= a and b <= e for s, e in marks)
        selected = bool(sel) and sel[0] <= a and b <= sel[1]
        piece = escape_markup(seg)
        if mark:
            piece = f"[color={MARK_COLOR}]{piece}[/color]"
        styled = prio or mark or selected or (select_mode and seg.strip())
        if styled:
            rid = len(refs)
            refs[rid] = {'start': a, 'end': b, 'hl': prio, 'mark': mark, 'sel': selected}
            piece = f"[ref={rid}]{piece}[/ref]"
        parts.append(piece)
    return ''.join(parts), refs


def atom_at(refs: Dict[int, dict], zones: Dict[str, list], tx: float, ty: float) -> Optional[Tuple[int, int]]:
    """Атом под точкой (tx, ty) — координаты внутри текстуры, ty считается СВЕРХУ.
       zones — label.refs из Kivy: {'3': [(x1, y1, x2, y2), …]}. Возвращает (start, end) или None."""
    for rid, boxes in zones.items():
        info = refs.get(int(rid))
        if info is None or not info['end'] > info['start']:
            continue
        for x1, y1, x2, y2 in boxes:
            if x1 <= tx <= x2 and y1 <= ty <= y2:
                return info['start'], info['end']
    return None


def sentence_bounds(text: str, a: int, b: int) -> Tuple[int, int]:
    """Границы предложения(й), в которых лежит выделение [a, b)."""
    s = a
    while s > 0 and not (text[s - 1].isspace() and s >= 2 and text[s - 2] in SENT_END):
        s -= 1
    e = max(b, a + 1)
    while e < len(text) and text[e - 1] not in SENT_END:
        e += 1
    while e < len(text) and text[e] in SENT_END + '»")':
        e += 1
    return s, min(e, len(text))
