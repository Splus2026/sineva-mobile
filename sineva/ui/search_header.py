"""Шапка страницы: «Страница N / Слова / Шрифт», переход на страницу, список слов."""
from kivy.metrics import dp
from kivy.metrics import sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.checkbox import CheckBox
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.slider import Slider
from kivy.uix.widget import Widget
from kivy.uix.textinput import TextInput
from ..search import escape_markup
from ..search import COLOR_LINK
from .widgets import TapCell
from .widgets import btn
from .widgets import mlabel
from .widgets import tall_popup
from .widgets import top_popup
from .theme import register

register(globals())


class HeaderMixin:
    """Шапка страницы: «Страница N / Слова / Шрифт», переход на страницу, список слов. Методы смешиваются в SearchScreen."""

    # ---------- шапка страницы: переход и слова ----------
    def _head_ref(self, ref):
        if ref == 'page':
            self._page_picker()
        elif ref == 'words' and not self.reading_screen:
            self._words_dialog()

    def _page_picker(self):
        cur, total = self.ctl.page_info()
        if total < 2:
            return
        box = BoxLayout(orientation='vertical', spacing=dp(8), padding=dp(8))
        inp = TextInput(text=str(cur), multiline=False, input_filter='int', size_hint_x=0.3, font_size=sp(18), halign='center')
        sl = Slider(min=1, max=total, value=cur, step=1, size_hint_y=None, height=dp(48))
        line = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        line.add_widget(mlabel('Страница', color=(1, 1, 1, 1), font_size=sp(16)))
        line.add_widget(inp)
        line.add_widget(mlabel(f"из {total}", color=(1, 1, 1, 1), font_size=sp(16)))
        box.add_widget(line)
        box.add_widget(sl)
        sync = {'busy': False}

        def from_slider(w, v):
            if not sync['busy']:
                sync['busy'] = True
                inp.text = str(int(v))
                sync['busy'] = False

        def from_text(w, v):
            if not sync['busy'] and v.isdigit():
                sync['busy'] = True
                sl.value = max(1, min(total, int(v)))
                sync['busy'] = False
        sl.bind(value=from_slider)
        inp.bind(text=from_text)
        pop = top_popup('Перейти на страницу', box, dp(250))

        def go_page(*_a):
            try:
                n = int(inp.text or cur)
            except ValueError:
                n = cur
            pop.dismiss()
            self.ctl.goto_page(max(1, min(total, n)))
        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        row.add_widget(btn('Перейти', go_page))
        row.add_widget(btn('Отмена', lambda: pop.dismiss(), color=GRAY))
        box.add_widget(row)
        inp.bind(on_text_validate=go_page)
        pop.open()

    def _words_dialog(self):
        rows = self.ctl.word_rows()
        if not rows:
            return
        only = self.ctl.search.only
        sel = {r['word'] for r in rows if r['word'] in only}
        box = BoxLayout(orientation='vertical', spacing=dp(6), padding=dp(8))
        box.add_widget(mlabel('Отметьте слова — останутся страницы только с ними', color=(1, 1, 1, 1), font_size=sp(14)))
        listing = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(2))
        listing.bind(minimum_height=listing.setter('height'))
        cbs, guard = [], {'on': False}
        all_line = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(6))
        all_cb = CheckBox(size_hint_x=None, width=dp(44), active=bool(rows) and len(sel) == len(rows))
        all_line.add_widget(all_cb)
        all_line.add_widget(Label(text='[b]Выбрать все[/b]', markup=True, halign='left', valign='middle',
                                  color=(1, 1, 1, 1), font_size=sp(16)))
        all_line.children[0].bind(size=lambda w, v: setattr(w, 'text_size', (v[0], v[1])))
        listing.add_widget(all_line)

        def all_toggled(w, v):
            if guard['on']:
                return
            guard['on'] = True
            for cb_ in cbs:
                cb_.active = v
            guard['on'] = False
        all_cb.bind(active=all_toggled)
        for r in rows:
            line = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(6))
            cb = CheckBox(size_hint_x=None, width=dp(44), active=r['word'] in sel)
            cbs.append(cb)

            def toggled(w, v, word=r['word']):
                (sel.add if v else sel.discard)(word)
                if not guard['on']:
                    guard['on'] = True
                    all_cb.active = len(sel) == len(rows)
                    guard['on'] = False
            cb.bind(active=toggled)
            lb = Label(text=f"{escape_markup(r['word'])} ({r['count']})", markup=True, halign='left', valign='middle',
                       color=(1, 1, 1, 1), font_size=sp(16))
            lb.bind(size=lambda w, v: setattr(w, 'text_size', (v[0], v[1])))
            line.add_widget(cb)
            line.add_widget(lb)
            listing.add_widget(line)
        sv = ScrollView()
        sv.add_widget(listing)
        box.add_widget(sv)
        pop = tall_popup('Слова', box)

        def apply(*_a):
            pop.dismiss()
            if sel:
                self.ctl.set_word_filter(sel)
            else:
                self.ctl.reset_filters()
                self.ctl.restate()
        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        row.add_widget(btn('Применить', apply))
        row.add_widget(btn('Сбросить', lambda: (sel.clear(), apply()), color=GRAY))
        row.add_widget(btn('Отмена', lambda: pop.dismiss(), color=GRAY))
        box.add_widget(row)
        pop.open()

    def _build_head(self, c, fs):
        """Шапка: две строки. Строка 1 — название (слева), «Слова» (посередине, только в поиске), «Шрифт: N» (справа).
           Строка 2 — мелкое («ур. N» / «из N») под названием и «+» «−» под левой и правой половинами «Шрифт: N»."""
        read = c.get('h_kind') == 'read'
        link = COLOR_LINK
        sub_fs = sp(max(11, round(fs * 0.75)))

        def lab(text, size, color, fill=False):
            lb = Label(text=text, markup=True, font_size=size, color=color, halign='center', valign='middle',
                       size_hint=(1 if fill else None, None))
            lb.bind(texture_size=lambda w, ts: setattr(w, 'height', ts[1]))
            if fill:
                lb.bind(width=lambda w, v: setattr(w, 'text_size', (v, None)))
            else:
                lb.bind(texture_size=lambda w, ts: setattr(w, 'width', ts[0]))
            return lb

        def cell(anchor_y='center', width=None):
            ac = TapCell(anchor_x='center', anchor_y=anchor_y, size_hint_x=1 if width is None else None)
            if width is not None:
                ac.width = width
            return ac

        # --- первая строка
        title_txt = c.get('h_title', '')
        T = lab('', sp(fs), ACCENT, not read)
        T.bind(on_ref_press=lambda w, ref: self._head_ref('page'))

        def set_title(*_):
            t = title_txt
            if not read:                                      # длинное название: не более 3 строк, конец заменяется «…»
                per_line = max(4, int(max(0, T.width - dp(14)) / (0.62 * sp(fs))))
                limit = 3 * per_line - 6
                if len(t) > limit:
                    t = t[:max(1, limit)].rstrip() + '…'
            T.text = f"[ref=page][b][color={link}]{escape_markup(t)}[/color][/b][/ref]"
        if not read:
            T.bind(width=set_title)
        set_title()
        F = lab(f"[b]Шрифт: {fs}[/b]", sp(fs), ACCENT)
        W = None if read else lab(f"[b][color={link}]Слова[/color][/b]", sp(fs), ACCENT)
        # --- вторая строка
        S = lab(c.get('h_sub', '') if read else f"[color=777777]ур. {c.get('h_level', 0)}[/color]", sub_fs, GRAY, not read)
        P = lab(f"[b][color={link}]+[/color][/b]", sp(52), ACCENT)
        M = lab(f"[b][color={link}]−[/color][/b]", sp(52), ACCENT)

        cT, cS = cell('top', None if not read else dp(40)), cell('center', None if not read else dp(40))
        cF = cell('top', dp(40))
        half_p, half_m = cell('center', dp(20)), cell('center', dp(20))
        cT.add_widget(T)
        cS.add_widget(S)
        cF.add_widget(F)
        half_p.add_widget(P)
        half_m.add_widget(M)
        half_p.on_tap = lambda: self.app.change_font_live(1)
        half_m.on_tap = lambda: self.app.change_font_live(-1)
        pm = BoxLayout(size_hint_x=None, width=dp(40))
        pm.add_widget(half_p)
        pm.add_widget(half_m)
        cW = cell('top', dp(60)) if W else None
        if W:
            cW.add_widget(W)
            cW.on_tap = lambda: self._head_ref('words')

        spW = Widget(size_hint_x=None, width=dp(60)) if W else None

        def colw(*_):
            rw = F.width + dp(12)
            cF.width = rw
            pm.width = rw
            half_p.width = half_m.width = rw / 2.0
            if W:
                cW.width = W.width + dp(30)
                spW.width = cW.width
            if read:
                cT.width = cS.width = max(T.width, S.width) + dp(6)
        T.bind(width=colw)
        S.bind(width=colw)
        F.bind(width=colw)

        row1 = BoxLayout(size_hint_y=None, height=dp(30))
        row2 = BoxLayout(size_hint_y=None, height=dp(30))
        row1.add_widget(cT)
        row2.add_widget(cS)
        if W:
            W.bind(width=colw)
            row1.add_widget(cW)
            row2.add_widget(spW)
        else:
            row1.add_widget(Widget())                          # распорка: правая колонка у правого края
            row2.add_widget(Widget())
        row1.add_widget(cF)
        row2.add_widget(pm)

        head = BoxLayout(orientation='vertical', size_hint_y=None, height=dp(60))

        def fit(*_):
            row1.height = max(T.height, F.height, W.height if W else 0, dp(24))
            row2.height = max(S.height, P.height, dp(24))
            head.height = row1.height + row2.height + dp(4)
        head.add_widget(row1)
        head.add_widget(row2)
        for w in (T, S, F, P, M) + ((W,) if W else ()):
            w.bind(height=fit)
        return head
