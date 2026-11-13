"""Экраны «Поиск» и «Чтение» (один класс SearchScreen)."""
import time
from kivy.clock import Clock
from kivy.graphics import Color
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from .theme import register
from .widgets import SelectableText, StatusDot, Track, clip_layers, keep_scroll, mlabel, no_bubble, vlist
from .search_gestures import GestureMixin
from .search_header import HeaderMixin
from .search_status import StatusMixin

register(globals())                      # цвета темы лежат обычными именами модуля


class SearchScreen(GestureMixin, HeaderMixin, StatusMixin, Screen):
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
    def set_undo(self, visible):
        """Вместо строки состояния — кнопки «Отмена», «+Шрифт», «+Цвет», «+Мысли»."""
        self.status_slot.clear_widgets()
        self.status_slot.add_widget(self.actions if visible else self.status_sv)

    def search(self):
        self.input.focus = False
        self.ctl.submit_query(self.input.text)

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
