"""Слои текста: подчёркивание, «+Шрифт», «+Цвет», свои мысли."""
from kivy.clock import Clock
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.textinput import TextInput
from ..search import escape_markup
from .widgets import PencilIcon, btn, icon_button, mlabel, no_bubble, top_popup
from .theme import register

register(globals())


class LayersMixin:
    """Слои текста: подчёркивание, «+Шрифт», «+Цвет», свои мысли. Методы смешиваются в SinevaApp."""

    # ---------- слои текста: подчёркивание, «+Шрифт», «+Цвет», «+Мысли» ----------
    _actions_pause = False          # после листания кнопки «Отмена/+Шрифт/…» скрыты до новой отметки или выделения
    cur_sel = None                  # последнее выделение: {'key', 's', 'e'} — в координатах текста карточки
    _undo_n = 0

    def _on_undo_count(self, n):
        self._undo_n = n
        self._actions_pause = False                      # новая отметка/отмена — кнопки снова нужны
        self.refresh_actions()

    def page_changed(self):
        """Страница сменилась: выделение сброшено, ряд кнопок над страницей убран (вместо него снова статус)."""
        self.cur_sel = None
        self._actions_pause = True
        self.refresh_actions()

    def refresh_actions(self):
        """Кнопки «Отмена / +Шрифт / +Цвет / +Мысли» видны, пока есть что отменять или выделено слово."""
        on = (self._undo_n > 0 or self.cur_sel is not None) and not self.ctl.marks_only() and not self._actions_pause
        for scr in (self.search_s, self.read_s):
            scr.set_undo(on)

    def refresh_layers(self, key=None):
        """Перерисовать слои карточки key (None — всех показанных)."""
        for scr in (self.search_s, self.read_s):
            keys = [key] if key else list(scr._widgets) or list(getattr(scr, '_keys', []))
            for k in keys:
                layers = self.ctl.card_layers(k)
                for w in scr._widgets.get(k, []):
                    w.set_layers(layers, w.card.get('offset', 0))
                    w.refresh()

    def _ro(self):
        if self.ctl.marks_only():
            self.ctl.on_status('Режим «Отметки»: только чтение и поиск, отметки менять нельзя')
            return True
        return False

    def on_anchor(self, key, ds, de, widget):
        if self._ro():
            return
        self.cur_sel = {'key': key, 's': ds, 'e': de}
        self._actions_pause = False
        self.refresh_actions()

    def undo_mark(self):
        if self._ro():
            return
        if not self.ctl.undo_stack:                         # нечего откатывать — просто снять выделение
            key = self.cur_sel['key'] if self.cur_sel else None
            self.cur_sel = None
            self.refresh_layers(key)
            self.refresh_actions()
            return
        key = self.ctl.undo_last_mark()
        self.cur_sel = None
        if key:
            self.refresh_layers(key)
        self.refresh_actions()

    def on_selection(self, key, start, end, widget):
        off = widget.card.get('offset', 0)                  # длинный текст состоит из нескольких кусков
        self.sel_done(key, start + off, end + off, widget.card['text'][start:end].strip())

    def sel_done(self, key, ds, de, text):
        """Выделен фрагмент (первое и последнее слово). Не подчёркнут — подчёркиваем и пишем в конспект;
           уже подчёркнут — диалог: снять выделение / +Шрифт / +Цвет / Отмена."""
        if self._ro():
            return
        self.cur_sel = {'key': key, 's': ds, 'e': de}
        self._actions_pause = False
        if self.ctl.selection_marked(key, ds, de):
            self.open_range_dialog()
            return
        result = self.ctl.toggle_mark(key, ds, de, text)
        self.refresh_layers(key)
        if result == 'marked':
            self.ctl.on_status('Подчёркнуто и записано в конспект. Дальше можно «+Шрифт» и «+Цвет»')
        self.refresh_actions()

    def _choice(self, title, rows, width_hint=1):
        """Небольшое окно с кнопками: rows — [(текст, действие, цвет)] или строки из таких списков."""
        box = BoxLayout(orientation='vertical', spacing=dp(6), padding=(dp(8), dp(6), dp(8), dp(6)))
        lines = [r if isinstance(r, list) else [r] for r in rows]
        pop = top_popup(title, box, dp(60) + dp(50) * len(lines))
        for line in lines:
            row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
            for item in line:
                label, fn, color = item
                b_ = btn(label, lambda fn=fn: (pop.dismiss(), fn()), color=color, markup=True)
                row.add_widget(b_)
            box.add_widget(row)
        pop.open()
        return pop

    def open_range_dialog(self):
        cs = self.cur_sel
        if not cs:
            return

        def unmark():
            text = ''
            result = self.ctl.toggle_mark(cs['key'], cs['s'], cs['e'], text)
            self.refresh_layers(cs['key'])
            if result == 'unmarked':
                self.ctl.on_status('Подчёркивание снято')
            self.refresh_actions()

        def cancel():
            self.cur_sel = None
            self.refresh_layers(cs['key'])
            self.refresh_actions()
        self._choice('Подчёркнутый фрагмент', [
            ('Снять выделение', unmark, ACCENT),
            [('+Шрифт', lambda: self.apply_style('big'), ACCENT), ('+Цвет', lambda: self.apply_style('color'), ACCENT)],
            ('Отмена', cancel, GRAY)])

    def apply_style(self, kind):
        if self._ro():
            return
        cs = self.cur_sel
        if not cs:
            self.ctl.on_status('Сначала нажмите первое и последнее слово фрагмента')
            return
        result = self.ctl.style_selection(kind, cs['key'], cs['s'], cs['e'])
        name = 'шрифт' if kind == 'big' else 'цвет'
        if result == 'need_mark':
            self.ctl.on_status('Шрифт и цвет — только внутри подчёркнутого: сначала подчеркните фрагмент')
            return
        if result is None:
            self.ctl.on_status('Не удалось выделить: попробуйте ещё раз')
            return
        self.refresh_layers(cs['key'])
        for scr in (self.search_s, self.read_s):
            scr.reveal(cs['key'], cs['s'])                  # изменённое место всегда остаётся на экране
        self.ctl.on_status(('Добавлен ' if result == 'on' else 'Снят ') + name)
        self.refresh_actions()

    # ---------- свои мысли ----------
    def open_note_editor(self, nid=None):
        """Окно ввода мыслей к выделенному слову (nid — правка уже сохранённой мысли)."""
        ctl = self.ctl
        if self._ro():
            return
        cs = self.cur_sel
        note = ctl.note_info(nid) if nid else None
        if note is None and not cs:
            ctl.on_status('Сначала нажмите слово, к которому относится мысль')
            return
        box = BoxLayout(orientation='vertical', spacing=dp(6), padding=(dp(8), dp(6), dp(8), dp(6)))
        field = no_bubble(TextInput(text=note.text if note else '', hint_text='Ваши мысли к этому слову',
                                    multiline=True, font_size=sp(16)))
        box.add_widget(field)
        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        pop = top_popup('Мои мысли' if not note else 'Правка мысли', box, dp(230))

        def save():
            text = field.text.strip()
            if not text:
                return
            if note:
                key = ctl.edit_note_text(nid, text)
            else:
                key = cs['key'] if ctl.add_note(cs['key'], cs['s'], cs['e'], text) else None
            pop.dismiss()
            self.refresh_layers(key)
            if key and cs:
                for scr in (self.search_s, self.read_s):
                    scr.reveal(key, cs['e'])
            self.refresh_actions()

        def delete():
            key = ctl.delete_note(nid)
            pop.dismiss()
            self.refresh_layers(key)
            self.refresh_actions()
        row.add_widget(btn('Сохранить', save))
        if note:
            row.add_widget(btn('Удалить', delete, color=GRAY))
        row.add_widget(btn('Отмена', pop.dismiss, color=GRAY))
        box.add_widget(row)
        pop.open()
        Clock.schedule_once(lambda dt: setattr(field, 'focus', True), 0.2)

    def on_note_tap(self, nid, widget=None):
        """Касание курсивной мысли: подчеркнуть (Ч) / +Шрифт / +Цвет / правка (карандаш)."""
        note = self.ctl.note_info(nid)
        if note is None or self._ro():
            return
        flags = note.flags or ''
        box = BoxLayout(orientation='vertical', spacing=dp(6), padding=(dp(8), dp(6), dp(8), dp(6)))
        box.add_widget(mlabel(escape_markup(note.text), color=(1, 1, 1, 1), font_size=sp(14)))
        pop = top_popup('Моя мысль', box, dp(220) + min(len(note.text), 200))
        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))

        def flag(f):
            key = self.ctl.toggle_note_flag(nid, f)
            self.refresh_layers(key)
            if key:
                for scr in (self.search_s, self.read_s):
                    scr.reveal(key, note_pos(key))
            self.refresh_actions()

        def note_pos(key):
            for p_, _t, _f, i_ in self.ctl.card_layers(key)['notes']:
                if i_ == nid:
                    return p_
            return 0
        for label, f in (('[u][b]Ч[/b][/u]', 'u'), ('+Шрифт', 'b'), ('+Цвет', 'c')):
            row.add_widget(btn(label, lambda f=f: (pop.dismiss(), flag(f)), markup=True,
                               color=ACCENT if f in flags else GRAY))
        row.add_widget(icon_button(PencilIcon(), lambda: (pop.dismiss(), self.open_note_editor(nid)), dp(56), BTN2))
        box.add_widget(row)
        box.add_widget(btn('Отмена', pop.dismiss, color=GRAY))
        pop.open()
