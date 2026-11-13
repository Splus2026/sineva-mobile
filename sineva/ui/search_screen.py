"""Экраны «Поиск» и «Чтение» (один класс SearchScreen)."""
import math
import time
from kivy.animation import Animation
from kivy.clock import Clock
from kivy.graphics import Color
from kivy.graphics import Rectangle
from kivy.metrics import dp
from kivy.metrics import sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen
from kivy.uix.scrollview import ScrollView
from kivy.uix.slider import Slider
from kivy.uix.widget import Widget
from kivy.uix.textinput import TextInput
from ..search import escape_markup
from ..search import COLOR_LINK
from .theme import register
from .widgets import SelectableText, StatusDot, TapCell, Track, btn, clip_layers, keep_scroll, mlabel, no_bubble, tall_popup, top_popup, vlist

register(globals())                      # цвета темы лежат обычными именами модуля


class SearchScreen(Screen):
    DECIDE_DP = 7          # путь пальца (dp), после которого определяется направление жеста
    HORIZ_MAX = 40         # угол от горизонтали (°): не больше — листание
    VERT_MIN = 60          # не меньше — прокрутка; между ними — диагональ, страница не двигается
    def __init__(self, app, reading=False, **kw):
        super().__init__(name='read' if reading else 'search', **kw)
        self.app, self.ctl, self.reading_screen = app, app.ctl, reading
        self._tok = 0
        self._keys = []                  # ключи показанных карточек (для дорисовки «Показать ещё»)
        self._cards = []                 # их виджеты
        self._widgets = {}               # ключ -> виджеты-куски текста
        self._pre = {}                   # заранее построенные соседние страницы {±1: ScrollView}
        self._peek_gen = 0               # поколение соседних страниц при перетаскивании
        self._drag = None                # состояние перетаскивания {'panels': {±1: ScrollView}, 'sy': …}
        self._anim_busy = False
        self._swiped = False             # блок сменился перетаскиванием: прежняя анимация листания не нужна
        self._cover = None               # соседняя страница, закрывающая место, пока строится новый блок
        self._last_pos = 0               # номер прежнего блока: по нему видно, в какую сторону перелистнули
        root = BoxLayout(orientation='vertical', spacing=dp(4), padding=dp(6))
        top = BoxLayout(size_hint_y=None, height=dp(56), spacing=dp(4))
        self.input = no_bubble(TextInput(hint_text='Введите слово', multiline=False,
                               font_size=sp(22), write_tab=False, background_normal='', background_active='',
                               background_color=INPUT_BG, foreground_color=TXT, cursor_color=TXT,
                               hint_text_color=(0.5, 0.52, 0.6, 1)))

        def fit(w, *a):
            """Высота и вертикальные отступы подбираются по высоте строки: верх и низ букв не срезаются."""
            lh = w.line_height if isinstance(w.line_height, (int, float)) else 30
            h = max(dp(56), lh + dp(22))
            w.height = h
            top.height = h
            pad = (h - lh) / 2
            w.padding = [dp(10), pad, dp(10), pad]
        self.input.size_hint_y = None
        self.input.bind(line_height=fit, font_size=fit)
        fit(self.input)
        self.input.bind(text=lambda w, v: self.ctl.on_query_changed(v))      # только запоминает запрос
        self.input.bind(on_text_validate=lambda *_: self.search())
        self.go = Button(text='Поиск', size_hint_x=None, width=dp(100), background_normal='',
                         background_color=ACCENT, color=(1, 1, 1, 1), font_size=sp(20))
        self.go.bind(on_release=lambda *_: self.search())
        top.add_widget(self.input)
        top.add_widget(self.go)
        # окно статуса фиксированной высоты (поиск — 3 строки, чтение — 2) с прокруткой: нижняя часть экрана не скачет
        self.status = mlabel('Выберите ровно одну книгу на вкладке «Книги» — страницы пойдут подряд.' if reading else
                             'Введите слово и нажмите «Поиск». Книги выбираются на вкладке «Книги».', font_size=sp(13))
        self.status.color = GRAY
        self.status_sv = ScrollView(size_hint_y=None, bar_width=dp(3), do_scroll_x=False,
                                    height=sp(13) * 1.3 * (2 if (reading or self.ctl.short_status()) else 3) + dp(10))
        self.status_sv.add_widget(self.status)
        self.status_slot = BoxLayout(orientation='vertical', size_hint_y=None, height=max(self.status_sv.height, dp(37)))
        self._m = {'draw': [None, None], 'swap': [None, None]}      # [первый экран, вся страница], мс
        self._chars = None                                           # (знаков в первом куске, знаков всего)
        self._sz = {'draw': [None, None], 'swap': [None, None]}      # размеры отрисовок, байт
        self._nb = {}                                                # соседи: {-1|1: (мс, байт)}
        self._nav = None
        self.sv, self.box = vlist()
        self.sv.scroll_distance = dp(10)                 # прокрутка начинается позже, чем распознаётся направление жеста
        self._active = None
        self._suffix = ''
        self._base = ''
        if not reading:
            root.add_widget(top)
        self.status_slot.add_widget(self.status_sv)
        self.dot = StatusDot()
        dot_col = BoxLayout(size_hint=(None, 1), width=dp(18), padding=(dp(2), dp(4), 0, 0))
        self.dot.pos_hint = {'top': 1}
        dot_col.add_widget(self.dot)
        status_row = self.status_row = BoxLayout(size_hint_y=None)
        status_row.add_widget(self.status_slot)
        status_row.add_widget(dot_col)
        self.status_slot.bind(height=lambda w, h: setattr(status_row, 'height', min(h, dp(84))))   # длинный статус не должен съедать место списка
        status_row.height = min(self.status_slot.height, dp(84))
        root.add_widget(status_row)
        self.actions = BoxLayout(size_hint_y=None, height=dp(37), spacing=dp(3))
        self.undo_btn = Button(text='Отмена', size_hint_x=0.62, background_normal='', background_color=ACCENT,
                               color=(1, 1, 1, 1), font_size=sp(14))
        self.undo_btn.bind(on_release=lambda *_: self.app.undo_mark())
        self.actions.add_widget(self.undo_btn)
        for label, cb in (('+Шрифт', lambda: self.app.apply_style('big')), ('+Цвет', lambda: self.app.apply_style('color')),
                          ('+Мысли', lambda: self.app.open_note_editor())):
            b_ = Button(text=label, background_normal='', background_color=ACCENT, color=(1, 1, 1, 1), font_size=sp(14))
            b_.bind(on_release=lambda *_, cb=cb: cb())
            self.actions.add_widget(b_)
        top.bind(height=lambda w, h: setattr(self.actions, 'height', h * 2 / 3))   # 2/3 высоты кнопки «Поиск»
        self.actions.height = top.height * 2 / 3
        self.page = FloatLayout()                        # «страница»: при листании выезжает сбоку
        self.sv.size_hint, self.sv.pos_hint = (1, 1), {'x': 0, 'y': 0}
        self.track = Track(size_hint=(1, 1))
        self.track.add_widget(self.sv)
        self.page.add_widget(self.track)
        root.add_widget(self.page)
        # листание блоков: «Назад» — «N из M» — «Далее» (то же делает свайп влево/вправо)
        self.navbar = BoxLayout(size_hint_y=None, height=0, spacing=dp(4), opacity=0)
        self.nav_prev = Button(text='Назад', size_hint_x=0.3, background_normal='', background_color=BTN2,
                               color=BTN2_TXT, font_size=sp(15))
        self.nav_prev.bind(on_release=lambda *_: self.ctl.go(-1))
        self.nav_lab = Label(text='', color=TXT, font_size=sp(15))
        self.nav_next = Button(text='Далее', size_hint_x=0.3, background_normal='', background_color=BTN2,
                               color=BTN2_TXT, font_size=sp(15))
        self.nav_next.bind(on_release=lambda *_: self.ctl.go(1))
        for w_ in (self.nav_prev, self.nav_lab, self.nav_next):
            self.navbar.add_widget(w_)
        # строка «Назад — N из M — Далее» убрана: листание свайпом (виджеты остаются для update_nav, но не показываются)
        self.add_widget(root)

    def update_nav(self, nav):
        total = nav['total'] if nav else 0
        on = total > 0
        self.navbar.height = dp(44) if on else 0
        self.navbar.opacity = 1 if on else 0
        self.nav_lab.text = f"{nav['pos']} из {total}" if on else ''
        for b_, flag in ((self.nav_prev, nav and nav['prev']), (self.nav_next, nav and nav['next'])):
            b_.disabled = not flag
            b_.opacity = 1 if flag else 0.35

    # листание: страница следует за пальцем, рядом виден соседний блок
    def on_touch_down(self, touch):
        self.sv.do_scroll_y = True                           # страховка: прокрутка не должна остаться выключенной
        if self.collide_point(*touch.pos) and not self._anim_busy:
            touch.ud['sw0'] = (touch.x, touch.y, time.time(), self.sv.scroll_y)
        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        st = touch.ud.get('sw0')
        if st and not touch.ud.get('pinch') and not self._anim_busy:
            dx, dy = touch.x - st[0], touch.y - st[1]
            if touch.ud.get('swd') is None and dx * dx + dy * dy > dp(self.DECIDE_DP) ** 2:
                ang = math.degrees(math.atan2(abs(dy), abs(dx)))      # угол от горизонтали
                if ang <= self.HORIZ_MAX:                                # почти горизонталь: листание, прокрутка заблокирована
                    touch.ud['swd'] = True
                    self.sv.scroll_y = st[3]
                    self._drag_begin()
                elif ang >= self.VERT_MIN:                              # почти вертикаль: прокрутка, листание заблокировано
                    touch.ud['swd'] = False
                # 30…60° (диагональ): ни листания, ни прокрутки, пока палец не выпрямится
            if touch.ud.get('swd'):
                edge = dp(self.DECIDE_DP) * 0.7                  # «мёртвая зона» распознавания вычитается: страница идёт плавно, без скачка
                self._drag_move(dx - math.copysign(min(abs(dx), edge), dx))
                return True
            if touch.ud.get('swd') is None:
                self.sv.scroll_y = st[3]                     # диагональ: страница не смещается
                return True
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        st = touch.ud.get('sw0')
        res = super().on_touch_up(touch)
        if st and touch.ud.get('swd'):
            self._drag_end(touch.x - st[0], time.time() - st[2])
            return True
        if st and touch.ud.get('swd') is None:
            try:
                self.sv.effect_y.velocity = 0                # диагональный жест не должен «доезжать» прокруткой
            except Exception:
                pass
            self.sv.scroll_y = st[3]
        return res

    def _clear_panels(self):
        if self._drag:
            for pv in self._drag['panels'].values():
                if pv is not None and pv.parent is not None and pv is not self._cover:
                    self.track.remove_widget(pv)
        self._drag = None

    def _drag_begin(self):
        Animation.cancel_all(self.sv)
        Animation.cancel_all(self.track)
        self.track.tx = 0
        for g in self.__dict__.get('_ghosts', []):
            if g.parent is not None:
                self.page.remove_widget(g)
        self._ghosts = []
        self._clear_panels()
        self.sv.do_scroll_y = False                          # на время перетаскивания вертикальная прокрутка выключена
        self._drag = {'panels': dict(self._pre), 'shown': None}      # соседи заранее построены — без паузы в начале жеста

    def _build_panel(self, sign):
        card = self.ctl.peek(sign)
        if card is None:
            return None
        t0 = time.time()
        pv, box = vlist()
        pv.size_hint, pv.pos_hint = (1, 1), {'x': float(sign), 'y': 0}
        w = self._card(card, None, None, peek=True)
        box.add_widget(w)
        ms = (time.time() - t0) * 1000

        def measured(dt):
            self._nb[sign] = (ms, self._tex_bytes(w))
            self._show_status()
        Clock.schedule_once(measured, 0.05)                   # текстуры появляются со следующего кадра
        return pv

    def _panel(self, sign):
        """Страница соседнего блока (+1 — следующий, -1 — предыдущий) или None."""
        d = self._drag
        if sign not in d['panels']:
            d['panels'][sign] = self._build_panel(sign)
        return d['panels'][sign]

    def _prepare_panels(self, gen, signs=(1, -1)):
        """Пока страницу читают, в паузах по одному строятся соседние: к началу свайпа они уже готовы."""
        if gen != self._peek_gen or self._drag is not None or not signs:
            return
        sign, rest = signs[0], signs[1:]
        if sign not in self._pre:
            self._pre[sign] = self._build_panel(sign)
        Clock.schedule_once(lambda dt: self._prepare_panels(gen, rest), 0.15)

    def _drag_move(self, dx):
        d = self._drag
        if d is None:
            return
        W = max(1.0, float(self.page.width))
        sign = 1 if dx < 0 else -1
        pv = self._panel(sign)
        if d['shown'] is not None and d['shown'] is not pv and d['shown'].parent is not None:
            self.track.remove_widget(d['shown'])
        d['shown'] = pv
        if pv is not None and pv.parent is None:
            self.track.add_widget(pv)                        # соседняя страница стоит рядом (±ширина), сдвигается вся лента
        self.track.tx = dx if pv is not None else dx * 0.3   # с краю списка страница упруго сопротивляется

    def _unlock_scroll(self):
        try:
            self.sv.effect_y.velocity = 0                    # никакого «долёта» прокрутки после жеста
        except Exception:
            pass
        self.sv.do_scroll_y = True

    def _drag_end(self, dx, dt):
        d = self._drag
        if d is None:
            return
        self._unlock_scroll()
        W = max(1.0, float(self.page.width))
        sign = 1 if dx < 0 else -1
        pv = d['panels'].get(sign)
        fast = abs(dx) > dp(50) and dt < 0.35
        commit = pv is not None and (abs(dx) > W * 0.28 or fast)
        self._anim_busy = True
        if commit:
            anim = Animation(tx=-sign * W, d=self._dur(), t='out_quad')

            def finish(*a):
                self._anim_busy = False
                self._cover = pv                              # закрывает место, пока новый блок строится под ним
                self._swiped = True                           # прежняя страница остаётся за экраном, пока строится новая
                self._drag = None
                self.ctl.go(sign)
                Clock.schedule_once(lambda dt: self._drop_cover(), 0.8)   # страховка, если блок не сменился
            anim.bind(on_complete=finish)
            anim.start(self.track)
        else:
            anim = Animation(tx=0, d=self._dur(), t='out_quad')

            def back(*a):
                self._anim_busy = False
                self._clear_panels()
                self.track.tx = 0
            anim.bind(on_complete=back)
            anim.start(self.track)

    def _drop_cover(self):
        pv, self._cover = self._cover, None
        self._swiped = False
        self.track.tx = 0
        if pv is not None and pv.parent is not None:
            self.track.remove_widget(pv)

    def refresh_card(self, key):
        """Слои карточки перерисовываются через refresh_layers."""

    def destroy(self):
        pass

    def _snapshot(self):
        """Снимок текущей страницы (картинка), чтобы она уезжала, пока под ней уже строится следующий блок."""
        try:
            img = self.sv.export_as_image()
            img.texture.flip_vertical()                      # снимок получается перевёрнутым
            ghost = Widget(size_hint=(1, 1), pos_hint={'x': 0, 'y': 0})
            with ghost.canvas:
                Color(1, 1, 1, 1)
                rect = Rectangle(texture=img.texture, pos=ghost.pos, size=ghost.size)
            ghost.bind(pos=lambda w, v: setattr(rect, 'pos', v), size=lambda w, v: setattr(rect, 'size', v))
            return ghost
        except Exception:
            return None

    def turn(self, direction, ghost=None):
        """Листание как в книге: прежний блок уезжает в сторону, а следом, без зазора, выезжает следующий.
           Вперёд — влево, назад — вправо."""
        Animation.cancel_all(self.sv)
        for g in self.__dict__.get('_ghosts', []):
            self.page.remove_widget(g)
        self._ghosts = []
        self.sv.opacity = 1
        self.sv.pos_hint = {'x': 1.0 * direction, 'y': 0}
        Animation(pos_hint={'x': 0, 'y': 0}, d=self._dur(), t='out_cubic').start(self.sv)
        if ghost is not None:
            self._ghosts = [ghost]
            self.page.add_widget(ghost)
            anim = Animation(pos_hint={'x': -1.0 * direction, 'y': 0}, d=self._dur(), t='out_cubic')
            anim.bind(on_complete=lambda *a: (self.page.remove_widget(ghost), self._ghosts.clear()) if ghost.parent else None)
            anim.start(ghost)

    def _dur(self):
        """Длительность анимации листания: не меньше порога из настроек."""
        return max(0.001, self.ctl.swap_min_ms() / 1000.0)

    def set_undo(self, visible):
        """Вместо строки состояния — кнопки «Отмена», «+Шрифт», «+Цвет», «+Мысли»."""
        self.status_slot.clear_widgets()
        self.status_slot.add_widget(self.actions if visible else self.status_sv)

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

    def search(self):
        self.input.focus = False
        self.ctl.submit_query(self.input.text)

    def set_status(self, text):
        self._base = text
        self._show_status()

    def apply_status_mode(self):
        """Высота окна статуса: сокращённый — 2 строки, полный — 3 (поиск) / 2 (чтение), везде с прокруткой."""
        short = self.ctl.short_status()
        lines = 2 if short else (2 if self.reading_screen else 3)
        self.status_sv.height = sp(13) * 1.3 * lines + dp(10)
        self.status_slot.height = max(self.status_sv.height, dp(37))
        self._show_status()

    @staticmethod
    def _tex_bytes(widget):
        """Размер текстур текста в виджете (RGBA, 4 байта на пиксель) — «размер отрисовки»."""
        total = 0
        try:
            for w in widget.walk():
                ts = getattr(w, 'texture_size', None)
                if ts and isinstance(w, Label):
                    total += int(ts[0]) * int(ts[1]) * 4
        except Exception:
            return None
        return total

    @staticmethod
    def _fmt_sz(a, b=None):
        """«120/340 Кб» (или Мб, если больше мегабайта); b=None — одно значение."""
        vals = [v for v in (a, b) if v is not None]
        if not vals:
            return ''
        big = max(vals) >= 1048576
        f = (lambda v: f"{v / 1048576:.1f}") if big else (lambda v: f"{v / 1024:.0f}")
        unit = 'Мб' if big else 'Кб'
        if b is None:
            return f"{f(a)} {unit}"
        return f"{f(a) if a is not None else f(b)}/{f(b)} {unit}"

    def _show_status(self):
        """Текст статуса: основное сообщение; в полном режиме — время (размер) отрисовки, «своп» и соседние страницы."""
        base, text = self._base, self._base
        if self.ctl.short_status():
            self.status.text = escape_markup(text)
            self.status_sv.scroll_y = 1
            return

        def pair(v):                                          # «первый экран/вся страница»; пока дорисовывается — «200/…»
            f, full = v
            return None if f is None and full is None else f"{(f if f is not None else full):.0f}/" + (f"{full:.0f}" if full is not None else '…')

        def sz(kind):
            f, full = self._sz[kind]
            r = self._fmt_sz(f, full) if (f is not None or full is not None) else ''
            return f" ({r})" if r else ''
        d, w = pair(self._m['draw']), pair(self._m['swap'])
        zn = f", {self._chars[0]}/{self._chars[1]} зн" if self._chars and (d or w) else ''
        if base.startswith('Поиск ') and not self.reading_screen:
            if d:
                full = self._m['draw'][1]
                text += f", отрисовка {d} мс{sz('draw')}{zn}" + (f". Итого {self.ctl.search_ms + full:.0f} мс" if full is not None else '')
            if w and self._nav:
                text += f" · своп {w} мс{sz('swap')}, стр. {self._nav['pos']} из {self._nav['total']}"
        elif ', стр. ' in base and self.reading_screen:
            if d:
                text += f", отрисовка {d} мс{sz('draw')}{zn}"
            if w:
                text += f", своп {w} мс{sz('swap')}"
        nb = []
        for sign, name in ((-1, 'пред.'), (1, 'след.')):
            v = self._nb.get(sign)
            if v:
                nb.append(f"{name} {v[0]:.0f} мс" + (f" ({self._fmt_sz(v[1])})" if v[1] is not None else ''))
        if nb and (d or w):
            text += ". Соседи: " + ', '.join(nb)
        self.status.text = escape_markup(text)
        self.status_sv.scroll_y = 1

    def show_cards(self, cards, total, nav):
        """Показывается один блок. Сначала текст первого экрана (жёлтый индикатор), остальные куски блока
           дорисовываются по одному на кадр; когда готов весь блок — индикатор зеленеет."""
        t0 = time.time()
        self.dot.set(None)
        ctl = self.ctl
        self._tok += 1
        tok = self._tok
        ctl.pending_append = None
        reading = ctl.reading
        fresh = bool(reading and reading.pop('fresh', False))
        keys = [c['key'] for c in cards]
        keys_changed = keys != self._keys
        if keys != self._keys:                               # другой блок: выделение прежнего больше не нужно
            self.app.page_changed()
        new_pos = nav['pos'] if nav else 0
        direction = (1 if new_pos > self._last_pos else -1) if (cards and self._last_pos and new_pos and new_pos != self._last_pos
                                                               and keys != self._keys) else 0
        self._last_pos = new_pos
        swiped = self._swiped
        self._nav = nav
        if keys != self._keys and cards:                      # замер: первая отрисовка — «отрисовка», при листании — «своп»
            self._pending_kind = 'swap' if direction else 'draw'
            self._m['swap'] = [None, None]
            self._sz['swap'] = [None, None]
            if not direction:
                self._m['draw'] = [None, None]
                self._sz['draw'] = [None, None]
        ghost = self._snapshot() if direction and not swiped else None
        self.update_nav(nav)

        def reset():
            self.box.clear_widgets()
            self._cards, self._widgets = [], {}
        if keys != self._keys:                               # новый блок: всегда с начала, прежнее положение прокрутки не переносится
            reset()
            self.sv.scroll_y = 1
            Clock.schedule_once(lambda dt: setattr(self.sv, 'scroll_y', 1), 0)
            Clock.schedule_once(lambda dt: setattr(self.sv, 'scroll_y', 1), 0.05)
        else:
            keep_scroll(self.sv, reset)
        self._keys = keys
        if not cards:
            if not self.reading_screen and ctl.query.strip():
                self.box.add_widget(mlabel('Ничего не найдено', color=GRAY))
            return

        kind = self.__dict__.get('_pending_kind') if (keys_changed) else None

        def done():
            if tok != self._tok:
                return
            built = (time.time() - t0) * 1000
            self.dot.set('green')
            if kind:
                self._m[kind][1] = built
                if self._m[kind][0] is None:
                    self._m[kind][0] = built
                self._show_status()

                def measure(dt):
                    if tok == self._tok:
                        self._sz[kind][1] = self._tex_bytes(self.box)
                        if self._sz[kind][0] is None:
                            self._sz[kind][0] = self._sz[kind][1]
                        self._show_status()
                Clock.schedule_once(measure, 0.05)

        def first_frame(dt):                                  # первый экран: первый кусок построен и показан кадром
            if tok == self._tok and kind and self._m[kind][0] is None:
                self._m[kind][0] = (time.time() - t0) * 1000
                self._sz[kind][0] = self._tex_bytes(self.box)
                self._show_status()
        c0 = cards[0]['chunks'][0]
        self._chars = (c0[1] - c0[0], len(cards[0]['text']))
        w = self._card(cards[0], tok, done)
        self._cards.append(w)
        self.box.add_widget(w)
        Clock.schedule_once(first_frame, 0)
        if swiped:
            Clock.schedule_once(lambda dt: self._drop_cover(), 0.12)
        elif direction:
            self.turn(direction, ghost)
        self._peek_gen += 1                                   # прежние заготовки соседей устарели (другая страница или шрифт)
        self._pre = {}
        self._nb = {}
        gen = self._peek_gen
        Clock.schedule_once(lambda dt: self._prepare_panels(gen), 0.7)

    def clear(self):
        """Очистка карточек без перерисовки (после смены шрифта или вида)."""
        self.dot.set(None)
        self.update_nav(None)
        self._last_pos = 0
        self.box.clear_widgets()
        self._keys, self._cards, self._widgets = [], [], {}

    def reveal(self, key, pos):
        """Показать на экране место карточки key (позиция pos в её тексте): прокрутка, если оно вне видимой части."""
        target = None
        for w in self._widgets.get(key, []):
            off = w.card.get('offset', 0)
            if off <= pos <= off + len(w.card['text']):
                target = (w, pos - off)
                break
        if target is None:
            return

        def go(dt):
            y = target[0].focus_y(target[1])
            sv = self.sv
            content = sv.children[0] if sv.children else None
            if y is None or content is None or content.height <= sv.height:
                return
            bottom = sv.to_window(sv.x, sv.y)[1]
            margin = dp(60)
            if bottom + margin <= y <= bottom + sv.height - margin:
                return
            dy = (bottom + sv.height / 2.0) - y                  # на сколько сдвинуть содержимое вверх
            sv.scroll_y = min(1, max(0, sv.scroll_y - dy / (content.height - sv.height)))
        Clock.schedule_once(go, 0.15)

    def _activate(self, widget):
        if self._active is not None and self._active is not widget:
            self._active.clear_selection()
        self._active = widget

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

    def _card(self, c, tok=None, on_done=None, peek=False):
        box = BoxLayout(orientation='vertical', size_hint_y=None, padding=dp(8), spacing=dp(2))
        box.bind(minimum_height=box.setter('height'))
        fs = self.ctl.config.get_int('font_size', 16)
        head = self._build_head(c, fs)
        key = c['key']
        box.add_widget(head)
        parts = []
        chunks = list(c['chunks'])
        if peek and chunks:                                # соседней странице нужна лишь верхняя часть — первый экран
            a0, b0 = chunks[0]
            if b0 - a0 > 900:
                cut = c['text'].rfind(' ', a0, a0 + 900)
                b0 = cut if cut > a0 else a0 + 900
            chunks = [(a0, b0)]

        def make(a_, b_):
            sub = dict(c, text=c['text'][a_:b_], offset=a_,
                       hl=[(max(s_, a_) - a_, min(e_, b_) - a_, p_) for s_, e_, p_ in c['hl'] if e_ > a_ and s_ < b_],
                       **clip_layers(c, a_, b_))
            w = SelectableText(sub, self.app.on_selection, self.ctl.on_status,
                               self.ctl.config.get_int('font_size', 16), self._activate,
                               on_note=self.app.on_note_tap, on_anchor=self.app.on_anchor, step=self.ctl.font_step(),
                               readonly=self.ctl.marks_only())
            parts.append(w)
            box.add_widget(w)

        gen = self._peek_gen
        make(*chunks.pop(0))                           # первый кусок — сразу (это первый экран)
        if chunks:
            if not peek:
                self.dot.set('yellow')

            def more_chunks(dt):
                if peek and gen != self._peek_gen:
                    return
                if not peek and tok is not None and tok != self._tok:
                    return
                make(*chunks.pop(0))
                if chunks:
                    Clock.schedule_once(more_chunks, 0)
                elif on_done:
                    on_done()
            Clock.schedule_once(more_chunks, 0)
        elif on_done:
            on_done()
        if not peek:
            self._widgets[key] = parts
        if self.ctl.config.get_bool('brief_view', False) and not self.ctl.reading:         # «Полностью» нужна только в кратком виде
            more = Button(text='Свернуть' if c['expanded'] else 'Полностью', size_hint_y=None, height=dp(34),
                          background_normal='', background_color=BTN2, color=BTN2_TXT)
            more.bind(on_release=lambda *_: self.ctl.toggle_expand(key))
            box.add_widget(more)
        from kivy.graphics import RoundedRectangle
        with box.canvas.before:
            Color(*CARD)
            rect = RoundedRectangle(radius=[dp(8)])
        box.bind(pos=lambda w, v: setattr(rect, 'pos', v), size=lambda w, v: setattr(rect, 'size', v))
        return box
