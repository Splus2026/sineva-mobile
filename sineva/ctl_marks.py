"""Выделение: подчёркивание, «+Шрифт», «+Цвет», мысли и конспект."""
import os
from typing import Optional
from . import marks as marks_mod
from . import notes


class MarksMixin:
    """Выделение: подчёркивание, «+Шрифт», «+Цвет», мысли и конспект. Методы смешиваются в Controller."""

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

    def swap_min_ms(self) -> int:
        """Нижний порог времени листания страницы (мс): анимация не короче этого значения."""
        return max(0, min(1000, self.config.get_int('swap_min_ms', 200)))

    def apply_font(self) -> None:
        """Новый размер шрифта сразу применяется к показанной странице (без сброса результатов)."""
        if self.outcome.results:
            self._publish()

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
