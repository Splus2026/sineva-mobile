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
MARK_ON_HL = '0a2a8c'                           # подчёркнутое поверх жёлтого/голубого фона
HL_TEXT = ''                                    # цвет шрифта на подложке; в ночной теме тёмный
STYLE_COLOR = '8a00a6'                          # «+Цвет»: пурпурный шрифт (в ночной теме светлее)
STYLE_COLOR_ON_HL = '5a0070'                    # то же поверх жёлтой подложки — тёмный пурпурный
NOTE_FLAGS = 'ubc'                              # u — подчёркнута, b — крупнее, c — цветная (для своей мысли)


def _covered(spans, a, b):
    return any(s <= a and b <= e for s, e in spans)


def build_markup(text: str, hl, marks, sel: Optional[Tuple[int, int]] = None,
                 select_mode: bool = False, big=(), color=(), notes=(), font_pt: int = 16,
                 step: int = 2) -> Tuple[str, Dict[int, dict]]:
    """hl — [(start, end, prio)], prio 2 — слово запроса, 1 — часть слова; marks — [(start, end)] подчёркнутое;
       sel — выделение пальцем; big / color — [(start, end)] «+Шрифт» / «+Цвет» (действуют только внутри подчёркнутого);
       notes — [(pos, текст, флаги, id)] свои мысли: показываются курсивом в скобках сразу после позиции pos.
       Возвращает (разметка, {ref_id: {'start','end','hl','mark','sel'[, 'note']}})."""
    n = len(text)
    cuts = {0, n}
    for s, e, _p in hl:
        cuts.update((s, e))
    for lst in (marks, big, color):
        for s, e in lst:
            cuts.update((s, e))
    note_at: Dict[int, list] = {}
    for item in notes:
        note_at.setdefault(min(max(item[0], 0), n), []).append(item)
    cuts.update(note_at)
    if sel:
        cuts.update(sel)
    if select_mode:
        for m in WORD_RE.finditer(text):
            cuts.update((m.start(), m.end()))
            stripped = m.group().rstrip()
            cuts.add(m.start() + len(stripped))          # слово отдельно от пробела
    cuts = sorted(c for c in cuts if 0 <= c <= n)
    parts, refs = [], {}
    big_size = int(font_pt) + int(step)

    def style_piece(piece, mark, is_big, is_col, prio_or_sel):
        if is_col:
            col = STYLE_COLOR_ON_HL if prio_or_sel else STYLE_COLOR
        elif prio_or_sel:                                # на яркой подложке шрифт всегда тёмный
            col = MARK_ON_HL if mark else HL_TEXT
        else:
            col = MARK_COLOR if mark else ''
        if col:
            piece = f"[color={col}]{piece}[/color]"
        if is_big:
            piece = f"[size={big_size}sp]{piece}[/size]"
        return piece

    def emit_notes(pos):
        for _p, ntext, flags, nid in note_at.get(pos, ()):
            mark = 'u' in flags
            piece = style_piece(f"[i] ({escape_markup(ntext)})[/i]", mark, 'b' in flags, 'c' in flags, False)
            rid = len(refs)
            refs[rid] = {'start': pos, 'end': pos, 'hl': 0, 'mark': mark, 'sel': False, 'note': nid}
            parts.append(f"[ref={rid}]{piece}[/ref]")

    if 0 in note_at:
        emit_notes(0)
    for a, b in zip(cuts, cuts[1:]):
        if a == b:
            continue
        seg = text[a:b]
        prio = max([p for s, e, p in hl if s <= a and b <= e], default=0)
        mark = _covered(marks, a, b)
        is_big = mark and _covered(big, a, b)                # размер и цвет — только внутри подчёркнутого
        is_col = mark and _covered(color, a, b)
        selected = bool(sel) and sel[0] <= a and b <= sel[1]
        piece = style_piece(escape_markup(seg), mark, is_big, is_col, bool(prio or selected))
        styled = prio or mark or selected or is_big or is_col or (select_mode and seg.strip())
        if styled:
            rid = len(refs)
            refs[rid] = {'start': a, 'end': b, 'hl': prio, 'mark': mark, 'sel': selected}
            piece = f"[ref={rid}]{piece}[/ref]"
        parts.append(piece)
        if b in note_at:
            emit_notes(b)
    return ''.join(parts), refs


def note_at_point(refs: Dict[int, dict], zones: Dict[str, list], tx: float, ty: float) -> Optional[int]:
    """Номер своей мысли под точкой (координаты как у atom_at) или None."""
    for rid, boxes in zones.items():
        info = refs.get(int(rid))
        if info is None or 'note' not in info:
            continue
        for x1, y1, x2, y2 in boxes:
            if x1 <= tx <= x2 and y1 <= ty <= y2:
                return info['note']
    return None


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
