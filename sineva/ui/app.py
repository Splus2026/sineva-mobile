"""Интерфейс «Синева Mobile» на Kivy. Вся логика — в sineva.controller.Controller; здесь только виджеты."""
import os
import time

from kivy.animation import Animation
from kivy.app import App
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.popup import Popup
from kivy.uix.progressbar import ProgressBar
from kivy.uix.screenmanager import NoTransition, Screen, ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.switch import Switch
from kivy.uix.widget import Widget
from kivy.uix.textinput import TextInput

from .. import __version__
from ..controller import Controller
from ..search import escape_markup
from .picker import add_home_shortcut, pick_files, publish_shared, share_file, save_to_downloads, is_android
from . import textmodel
from .splash import Splash
from . import safsync
from .textmodel import atom_at, build_markup, note_at_point

THEMES = {
    'day': dict(BG=(0.91, 0.94, 0.98, 1), CARD=(0.78, 0.85, 0.93, 1), TXT=(0.08, 0.13, 0.24, 1),
                BODY=(0.05, 0.08, 0.15, 1), ACCENT=(0.25, 0.42, 0.66, 1), GRAY=(0.45, 0.5, 0.6, 1),
                NAV=(0.84, 0.89, 0.96, 1), NAV_TXT=(0.25, 0.42, 0.66, 1), BTN2=(0.9, 0.93, 0.98, 1),
                BTN2_TXT=(0.25, 0.42, 0.66, 1), INPUT_BG=(1, 1, 1, 1), MARK_RGBA=(0.04, 0.16, 0.55, 1), MARK_COLOR='0a2a8c', HL_TEXT='',
                POP_BG=(0.17, 0.25, 0.42, 1), PANEL_BG=(0.8, 0.86, 0.94, 1), STYLE_COLOR='8a00a6'),
    'night': dict(BG=(0.12, 0.13, 0.19, 1), CARD=(0.24, 0.29, 0.48, 1), TXT=(0.93, 0.94, 0.98, 1),
                  BODY=(0.93, 0.94, 0.98, 1), ACCENT=(0.33, 0.42, 0.7, 1), GRAY=(0.4, 0.43, 0.56, 1),
                  NAV=(0.09, 0.1, 0.15, 1), NAV_TXT=(0.75, 0.8, 0.96, 1), BTN2=(0.19, 0.22, 0.36, 1),
                  BTN2_TXT=(0.8, 0.85, 1, 1), INPUT_BG=(0.17, 0.2, 0.32, 1), MARK_RGBA=(0.62, 0.78, 1, 1), MARK_COLOR='9ec7ff', HL_TEXT='000000',
                  POP_BG=(0.21, 0.17, 0.35, 1), PANEL_BG=(0.17, 0.19, 0.31, 1), STYLE_COLOR='ff7aeb'),
}
POP_BG = PANEL_BG = INPUT_BG = BG = CARD = TXT = BODY = ACCENT = GRAY = NAV = NAV_TXT = BTN2 = BTN2_TXT = None


def apply_theme(name):
    """Переключает цвета (глобальные переменные модуля); интерфейс после этого нужно пересоздать."""
    g = globals()
    t = THEMES.get(name, THEMES['day'])
    for k, v in t.items():
        if k not in ('MARK_COLOR', 'HL_TEXT', 'STYLE_COLOR'):
            g[k] = v
    textmodel.MARK_COLOR = t['MARK_COLOR']
    textmodel.HL_TEXT = t['HL_TEXT']
    textmodel.STYLE_COLOR = t['STYLE_COLOR']


apply_theme('day')
YELLOW = (1, 1, 0, 1)                    # слово запроса: #FFFF00 (как на ПК)
YELLOW_SOFT = (1, 1, 0.6, 1)             # часть слова: #FFFF99
SEL_BLUE = (0.67, 0.8, 1, 1)             # выделение пальцем: #AACCFF

SEPARATORS = ['N1000', 'N500', 'N2000', 'NS300', 'NS800', 'Numeric', 'ExcelRow1']


def style_popup(pop):
    """Окна поверх главного — другого оттенка (плоский фон POP_BG и акцентная линия под заголовком)."""
    pop.background = ''
    pop.background_color = POP_BG
    pop.separator_color = ACCENT
    pop.title_color = (1, 1, 1, 1)
    return pop


def top_popup(title, content, height):
    """Диалог в верхней части экрана (не выше половины): клавиатура снизу его не закрывает."""
    h = min(height, Window.height * 0.5)
    return style_popup(Popup(title=title, content=content, size_hint=(1, None), height=h, pos_hint={'top': 1},
                             auto_dismiss=False, title_size=sp(15), separator_height=dp(2)))


def tall_popup(title, content):
    """Диалог от верха экрана до строки вкладок Поиск/Книги/Ещё."""
    pop = style_popup(Popup(title=title, content=content, size_hint=(1, None), pos_hint={'top': 1},
                            auto_dismiss=False, title_size=sp(15), separator_height=dp(2)))
    fit = lambda *a: setattr(pop, 'height', max(dp(200), Window.height - dp(52)))
    fit()
    Window.bind(size=fit)
    pop.bind(on_dismiss=lambda *a: Window.unbind(size=fit))
    return pop


class ClockTimer:
    """Объект с .cancel() для задержки ввода в Controller."""
    def __init__(self, fn, delay):
        self._ev = Clock.schedule_once(lambda dt: fn(), delay)

    def cancel(self):
        self._ev.cancel()


def schedule(fn, delay):
    return ClockTimer(fn, delay)


def ui_post(fn):
    Clock.schedule_once(lambda dt: fn(), 0)


def btn(text, cb, **kw):
    b = Button(text=text, size_hint_y=None, height=dp(44), background_normal='',
               background_color=kw.pop('color', ACCENT), color=(1, 1, 1, 1), **kw)
    b.bind(on_release=lambda *_: cb())
    return b


def mlabel(text='', **kw):
    """Метка с разметкой, автоматически подбирающая высоту по тексту."""
    kw.setdefault('color', TXT)
    lb = Label(text=text, markup=True, size_hint_y=None, halign='left', valign='top',
               font_size=kw.pop('font_size', sp(16)), **kw)
    lb.bind(width=lambda w, v: setattr(w, 'text_size', (v, None)))
    lb.bind(texture_size=lambda w, ts: setattr(w, 'height', ts[1] + dp(8)))
    return lb


def keep_scroll(sv: ScrollView, rebuild, shift=0):
    """Перестраивает содержимое, сохраняя положение прокрутки в пикселях (список не прыгает в начало)."""
    content = sv.children[0] if sv.children else None
    if content is None:
        rebuild()
        return
    off = max(0, (1 - sv.scroll_y) * max(0, content.height - sv.height) - shift)
    rebuild()

    def restore(dt):
        c = sv.children[0]
        span = max(1, c.height - sv.height)
        sv.scroll_y = 1 - min(1, off / span) if c.height > sv.height else 1
    Clock.schedule_once(restore, 0)
    Clock.schedule_once(restore, 0.05)


def vlist():
    sv = ScrollView(bar_width=dp(4))
    box = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(6), padding=dp(6))
    box.bind(minimum_height=box.setter('height'))
    sv.add_widget(box)
    return sv, box


class ReadBox(ScrollView):
    """Только для чтения, с прокруткой по вертикали (если текст не помещается). to_end — показывать конец."""
    def __init__(self, to_end=False, font_pt=14, **kw):
        super().__init__(bar_width=dp(4), **kw)
        self.to_end = to_end
        self.lb = Label(color=TXT, size_hint_y=None, halign='left', valign='top', font_size=sp(font_pt))
        self.lb.bind(width=lambda w, v: setattr(w, 'text_size', (v - dp(8), None)))
        self.lb.bind(texture_size=lambda w, ts: setattr(w, 'height', ts[1] + dp(10)))
        self.add_widget(self.lb)
        with self.canvas.before:
            Color(*INPUT_BG)
            self._bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=lambda w, v: setattr(self._bg, 'pos', v), size=lambda w, v: setattr(self._bg, 'size', v))

    @property
    def text(self):
        return self.lb.text

    @text.setter
    def text(self, value):
        self.lb.text = value
        Clock.schedule_once(lambda dt: setattr(self, 'scroll_y', 0 if self.to_end else 1), 0.1)


def no_bubble(ti):
    """Без системной панели Copy/Cut/Paste и «капель» выделения поверх окон."""
    ti.use_bubble = False
    ti.use_handles = False
    ti.background_normal = ti.background_active = ''             # цвета по теме (в ночном режиме поле не белое)
    ti.background_color = INPUT_BG
    ti.foreground_color = TXT
    ti.cursor_color = TXT
    ti.hint_text_color = (0.5, 0.52, 0.6, 1)
    return ti


def clip_spans(spans, a, b):
    """Участки (начало, конец) в координатах всего текста -> в координатах куска [a, b)."""
    return [(max(s_, a) - a, min(e_, b) - a) for s_, e_ in spans if e_ > a and s_ < b]


def clip_layers(layers, a, b):
    """Слои всей карточки -> слои куска [a, b): подчёркнутое, шрифт, цвет и мысли (мысль — в куске, где она заканчивается)."""
    return {'marks': clip_spans(layers['marks'], a, b), 'big': clip_spans(layers['big'], a, b),
            'color': clip_spans(layers['color'], a, b),
            'notes': [(p - a, t, f, i) for p, t, f, i in layers['notes'] if a < p <= b or (p == 0 and a == 0)]}


class SelectableText(Label):
    """Текст топика: жёлтый маркер на найденных словах, подчёркнутое и выделение.
       Касание слова (без сдвига пальца) выделяет это слово, касание второго слова — весь фрагмент между ними;
       тогда вызывается on_select(card_key, start, end, виджет). Прокрутка работает как обычно."""

    TAP_MAX_MOVE = 12            # dp: больше — это уже прокрутка, а не касание

    def __init__(self, card, on_select, on_hint=lambda t: None, font_pt=16, on_activate=lambda w: None,
                 on_note=None, on_anchor=None, step=2, readonly=False, **kw):
        kw.setdefault('color', BODY)
        super().__init__(markup=True, size_hint_y=None, halign='left', valign='top', font_size=sp(font_pt), **kw)
        self.card, self.on_select, self.on_hint, self.on_activate = card, on_select, on_hint, on_activate
        self.on_note, self.on_anchor, self.font_pt, self.step = on_note, on_anchor, font_pt, step
        self.readonly = readonly                      # режим «Отметки»: только чтение
        self.sel = None
        self.select_mode = False
        self._refs_info = {}
        self._anchor = None
        self.bind(width=lambda w, v: setattr(w, 'text_size', (v, None)))
        self.bind(texture_size=self._on_texture)
        self._rects = []
        self.bind(pos=lambda *_: self._move_rects(), size=lambda *_: self._redraw())
        self._refresh()

    # --- разметка и подложки
    def _refresh(self):
        c = self.card
        self.text, self._refs_info = build_markup(c['text'], c['hl'], c['marks'], self.sel, self.select_mode,
                                                  c.get('big', ()), c.get('color', ()), c.get('notes', ()),
                                                  self.font_pt, self.step)

    def _apply(self):
        """Один пересчёт текстуры после смены разметки."""
        self._refresh()
        self.texture_update()
        self._redraw()

    def _on_texture(self, w, ts):
        self.height = ts[1] + dp(10)
        self._redraw()

    def _redraw(self):
        """Прямоугольники подложки пересоздаются только при смене текста/размера; при прокрутке (смене pos)
           они лишь сдвигаются — иначе на телефоне каждый кадр прокрутки перестраивал бы все карточки."""
        self.canvas.before.clear()
        self._rects = []
        ts = self.texture_size
        if not ts[1]:
            return
        ox = self.center_x - ts[0] / 2.0
        oy = self.center_y + ts[1] / 2.0
        with self.canvas.before:
            for rid, boxes in self.refs.items():
                info = self._refs_info.get(int(rid))
                if not info:
                    continue
                if info['sel']:
                    color = SEL_BLUE
                elif info['hl'] == 2:
                    color = YELLOW
                elif info['hl'] == 1:
                    color = YELLOW_SOFT
                else:
                    continue
                Color(*color)
                for x1, y1, x2, y2 in boxes:
                    r = Rectangle(pos=(ox + x1, oy - y2), size=(x2 - x1, y2 - y1))
                    self._rects.append((r, ox + x1 - self.x, oy - y2 - self.y))
            for rid, boxes in self.refs.items():                       # подчёркнутое: жирная тёмно-синяя линия
                info = self._refs_info.get(int(rid))
                if not info or not info['mark']:
                    continue
                Color(*MARK_RGBA)
                for x1, y1, x2, y2 in boxes:
                    r = Rectangle(pos=(ox + x1, oy - y2), size=(x2 - x1, dp(3)))
                    self._rects.append((r, ox + x1 - self.x, oy - y2 - self.y))

    def _move_rects(self):
        for r, dx, dy in self._rects:
            r.pos = (self.x + dx, self.y + dy)

    def _hit(self, pos):
        ts = self.texture_size
        tx = pos[0] - (self.center_x - ts[0] / 2.0)
        ty = ts[1] - (pos[1] - (self.center_y - ts[1] / 2.0))
        return atom_at(self._refs_info, self.refs, tx, ty)

    # --- касания
    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            touch.ud['tap_start_' + str(id(self))] = touch.pos       # в системе координат этого виджета
        return super().on_touch_down(touch)

    def on_touch_up(self, touch):
        start = touch.ud.get('tap_start_' + str(id(self)))
        if (start is not None and self.collide_point(*touch.pos) and not touch.ud.get('pinch') and
                (touch.pos[0] - start[0]) ** 2 + (touch.pos[1] - start[1]) ** 2 <= dp(self.TAP_MAX_MOVE) ** 2):
            self._tap(touch.pos)
        return super().on_touch_up(touch)

    def set_layers(self, layers, off):
        """Новые слои (подчёркнутое, шрифт, цвет, мысли) для этого куска карточки."""
        self.card.update(clip_layers(layers, off, off + len(self.card['text'])))

    def _note_hit(self, pos):
        ts = self.texture_size
        tx = pos[0] - (self.center_x - ts[0] / 2.0)
        ty = ts[1] - (pos[1] - (self.center_y - ts[1] / 2.0))
        return note_at_point(self._refs_info, self.refs, tx, ty)

    def focus_y(self, idx):
        """Координата y в окне того места текста, где находится позиция idx (для прокрутки к нему)."""
        ts = self.texture_size
        oy = self.center_y + ts[1] / 2.0
        best = None
        for rid, boxes in self.refs.items():
            info = self._refs_info.get(int(rid))
            if not info or not boxes:
                continue
            if info['start'] <= idx <= info['end']:
                best = boxes[0]
                if info['start'] < idx < info['end'] or info['end'] > info['start']:
                    break
        if best is None:
            return None
        return self.to_window(self.x, oy - (best[1] + best[3]) / 2.0)[1]

    def _tap(self, pos):
        if self.readonly:
            return
        nid = self._note_hit(pos)
        if nid is not None and self.on_note is not None:      # касание своей мысли
            self.on_note(nid, self)
            return
        if not self.select_mode:                  # первое касание карточки: слова становятся «нажимаемыми»
            self.on_activate(self)                # у остальных карточек выделение сбрасывается
            self.select_mode = True
            self._apply()
        word = self._hit(pos)
        if word is None or not self.card['text'][word[0]:word[1]].strip():
            return
        if self._anchor is None:
            self._anchor = word
            self.sel = word
            self._apply()
            self.on_hint('Нажмите последнее слово фрагмента (то же слово — выделить одно)')
            if self.on_anchor is not None:
                off = self.card.get('offset', 0)
                self.on_anchor(self.card['key'], word[0] + off, word[1] + off, self)
            return
        sel = (min(self._anchor[0], word[0]), max(self._anchor[1], word[1]))
        self._anchor = None
        self.sel = sel
        self._apply()
        self.on_select(self.card['key'], sel[0], sel[1], self)

    def refresh(self):
        """Принудительная перерисовка (после отмены выделения кнопкой)."""
        self.sel = None
        self._anchor = None
        self.select_mode = False
        self._apply()

    def clear_selection(self):
        self.sel = None
        self._anchor = None
        if self.select_mode:
            self.select_mode = False
            self._apply()


# ===================================================================
class StatusDot(Widget):
    """Маленький кружок: жёлтый — первая карточка нарисована, зелёный — нарисованы все."""
    def __init__(self, **kw):
        super().__init__(size_hint=(None, None), size=(dp(14), dp(14)), pos_hint={'top': 1}, **kw)
        self.rgba = None
        self.bind(pos=self._draw, size=self._draw)

    def set(self, state):
        self.rgba = {'yellow': (1, 0.85, 0, 1), 'green': (0.2, 0.75, 0.3, 1)}.get(state)
        self._draw()

    def _draw(self, *a):
        self.canvas.clear()
        if self.rgba:
            with self.canvas:
                Color(*self.rgba)
                from kivy.graphics import Ellipse
                Ellipse(pos=self.pos, size=self.size)


class SearchScreen(Screen):
    def __init__(self, app, reading=False, **kw):
        super().__init__(name='read' if reading else 'search', **kw)
        self.app, self.ctl, self.reading_screen = app, app.ctl, reading
        self._tok = 0
        self._keys = []                  # ключи показанных карточек (для дорисовки «Показать ещё»)
        self._cards = []                 # их виджеты
        self._widgets = {}               # ключ -> виджеты-куски текста
        self._fontbar_on = False
        self._last_tap = None
        self._fontbar_ev = None
        self._undo_visible = False
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
                                    height=sp(13) * 1.3 * (2 if reading else 3) + dp(10))
        self.status_sv.add_widget(self.status)
        self.status_slot = BoxLayout(orientation='vertical', size_hint_y=None, height=max(self.status_sv.height, dp(37)))
        self._m = {'draw': [None, None], 'swap': [None, None]}      # [первый экран, вся страница], мс
        self._chars = None                                           # (знаков в первом куске, знаков всего)
        self._nav = None
        self.sv, self.box = vlist()
        self._active = None
        self._suffix = ''
        self._base = ''
        if not reading:
            root.add_widget(top)
        self.status_slot.add_widget(self.status_sv)
        self.dot = StatusDot()
        dot_col = BoxLayout(size_hint=(None, 1), width=dp(18), padding=(dp(2), dp(4), 0, 0))
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
        self.page.add_widget(self.sv)
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
        if self.status_slot.collide_point(*touch.pos):
            # своя проверка двойного касания: у встроенной (is_double_tap) допуск 20 пикселей — пальцем не попасть
            now, last = time.time(), self._last_tap
            if last and now - last[0] < 0.5 and abs(touch.x - last[1]) < dp(70) and abs(touch.y - last[2]) < dp(70):
                self._last_tap = None
                if self._fontbar_on:
                    self._fontbar_hide()
                else:
                    self._fontbar_show()
                return True
            self._last_tap = (now, touch.x, touch.y)
        if self.collide_point(*touch.pos) and not self._anim_busy:
            touch.ud['sw0'] = (touch.x, touch.y, time.time(), self.sv.scroll_y)
        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        st = touch.ud.get('sw0')
        if st and not touch.ud.get('pinch') and not self._anim_busy:
            dx, dy = touch.x - st[0], touch.y - st[1]
            if touch.ud.get('swd') is None:
                if abs(dx) > dp(14) and abs(dx) > 1.5 * abs(dy):
                    touch.ud['swd'] = True
                    self._drag_begin()
                elif abs(dy) > dp(14):
                    touch.ud['swd'] = False
            if touch.ud.get('swd'):
                self.sv.scroll_y = st[3]                    # во время перетаскивания текст по вертикали не едет
                self._drag_move(dx)
                return True
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        st = touch.ud.get('sw0')
        res = super().on_touch_up(touch)
        if st and touch.ud.get('swd'):
            self._drag_end(touch.x - st[0], time.time() - st[2])
            return True
        return res

    def _clear_panels(self):
        self._peek_gen += 1
        if self._drag:
            for pv in self._drag['panels'].values():
                if pv is not None and pv.parent is not None and pv is not self._cover:
                    self.page.remove_widget(pv)
        self._drag = None

    def _drag_begin(self):
        Animation.cancel_all(self.sv)
        for g in self.__dict__.get('_ghosts', []):
            if g.parent is not None:
                self.page.remove_widget(g)
        self._ghosts = []
        self._clear_panels()
        self._drag = {'panels': {}, 'shown': None}

    def _panel(self, sign):
        """Страница соседнего блока (+1 — следующий, -1 — предыдущий) или None."""
        d = self._drag
        if sign not in d['panels']:
            card = self.ctl.peek(sign)
            pv = None
            if card is not None:
                pv, box = vlist()
                pv.size_hint, pv.pos_hint = (1, 1), {'x': sign, 'y': 0}
                box.add_widget(self._card(card, None, None, peek=True))
            d['panels'][sign] = pv
        return d['panels'][sign]

    def _drag_move(self, dx):
        d = self._drag
        if d is None:
            return
        W = max(1.0, float(self.page.width))
        sign = 1 if dx < 0 else -1
        pv = self._panel(sign)
        if d['shown'] is not None and d['shown'] is not pv and d['shown'].parent is not None:
            self.page.remove_widget(d['shown'])
        d['shown'] = pv
        off = dx if pv is not None else dx * 0.3             # с краю списка страница упруго сопротивляется
        self.sv.pos_hint = {'x': off / W, 'y': 0}
        if pv is not None:
            if pv.parent is None:
                self.page.add_widget(pv)
            pv.pos_hint = {'x': off / W + sign, 'y': 0}

    def _drag_end(self, dx, dt):
        d = self._drag
        if d is None:
            return
        W = max(1.0, float(self.page.width))
        sign = 1 if dx < 0 else -1
        pv = d['panels'].get(sign)
        fast = abs(dx) > dp(50) and dt < 0.35
        commit = pv is not None and (abs(dx) > W * 0.28 or fast)
        self._anim_busy = True
        if commit:
            Animation(pos_hint={'x': -sign, 'y': 0}, d=self._dur(), t='out_quad').start(self.sv)
            anim = Animation(pos_hint={'x': 0, 'y': 0}, d=self._dur(), t='out_quad')

            def finish(*a):
                self._anim_busy = False
                self._cover = pv                              # закрывает место, пока новый блок строится под ним
                self._swiped = True
                self.sv.pos_hint = {'x': 0, 'y': 0}
                self._drag = None
                self.ctl.go(sign)
                Clock.schedule_once(lambda dt: self._drop_cover(), 0.8)   # страховка, если блок не сменился
            anim.bind(on_complete=finish)
            anim.start(pv)
        else:
            Animation(pos_hint={'x': 0, 'y': 0}, d=self._dur(), t='out_quad').start(self.sv)
            shown = d['shown']
            anim = Animation(pos_hint={'x': float(sign), 'y': 0}, d=self._dur(), t='out_quad')

            def back(*a):
                self._anim_busy = False
                self._clear_panels()
                self.sv.pos_hint = {'x': 0, 'y': 0}
            anim.bind(on_complete=back)
            if shown is not None and shown.parent is not None:
                anim.start(shown)
            else:
                Clock.schedule_once(back, 0.18)

    def _drop_cover(self):
        pv, self._cover = self._cover, None
        self._swiped = False
        if pv is not None and pv.parent is not None:
            self.page.remove_widget(pv)

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
        self._undo_visible = visible
        if not self._fontbar_on:
            self._restore_slot()

    def _restore_slot(self):
        self.status_slot.clear_widgets()
        self.status_slot.add_widget(self.actions if self._undo_visible else self.status_sv)

    # двойное быстрое касание окна статуса: вместо текста — «Размер шрифта» и кнопки − / +
    def _make_fontbar(self):
        h = self.status_slot.height
        bar = BoxLayout(size_hint_y=None, height=h, spacing=dp(6))
        self._font_lbl = mlabel(f"Размер шрифта: {self.ctl.config.get_int('font_size', 16)}", font_size=sp(14))
        self._font_lbl.valign = 'middle'
        self._font_lbl.bind(size=lambda w, v: setattr(w, 'text_size', (v[0], v[1])))
        self._font_lbl.size_hint_y = 1
        bar.add_widget(self._font_lbl)
        for txt, dlt in (('−', -1), ('+', 1)):
            bar.add_widget(btn(txt, lambda d=dlt: self._font_change(d), size_hint_x=None, width=dp(56)))
        bar.add_widget(btn('Ок', self._fontbar_hide, size_hint_x=None, width=dp(56), color=GRAY))
        return bar

    def _font_change(self, delta):
        self._font_lbl.text = f"Размер шрифта: {self.app.change_font_live(delta)}"
        self._fontbar_timer()

    def _fontbar_timer(self):
        if self._fontbar_ev is not None:
            self._fontbar_ev.cancel()
        self._fontbar_ev = Clock.schedule_once(lambda dt: self._fontbar_hide(), 6)

    def _fontbar_show(self):
        self._fontbar_on = True
        self.status_slot.clear_widgets()
        self._fontbar = self._make_fontbar()
        self.status_slot.add_widget(self._fontbar)
        self._fontbar_timer()

    def _fontbar_hide(self):
        if self._fontbar_ev is not None:
            self._fontbar_ev.cancel()
            self._fontbar_ev = None
        self._fontbar_on = False
        self._restore_slot()

    def search(self):
        self.input.focus = False
        self.ctl.submit_query(self.input.text)

    def set_status(self, text):
        self._base = text
        self._show_status()

    def _show_status(self):
        """Текст статуса: основное сообщение + время отрисовки/«своп» (только для сообщений о поиске и странице)."""
        base, text = self._base, self._base
        def pair(v):                                          # «первый экран/вся страница»; пока дорисовывается — «200/…»
            f, full = v
            return None if f is None and full is None else f"{(f if f is not None else full):.0f}/" + (f"{full:.0f}" if full is not None else '…')
        d, w = pair(self._m['draw']), pair(self._m['swap'])
        zn = f", {self._chars[0]}/{self._chars[1]} зн" if self._chars and (d or w) else ''
        if base.startswith('Поиск ') and not self.reading_screen:
            if d:
                full = self._m['draw'][1]
                text += f", отрисовка {d} мс{zn}" + (f". Итого {self.ctl.search_ms + full:.0f} мс" if full is not None else '')
            if w and self._nav:
                text += f" · своп {w} мс, стр. {self._nav['pos']} из {self._nav['total']}"
        elif ', стр. ' in base and self.reading_screen:
            if d:
                text += f", отрисовка {d} мс{zn}"
            if w:
                text += f", своп {w} мс"
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
            self.app.cur_sel = None
            self.app.refresh_actions()
        new_pos = nav['pos'] if nav else 0
        direction = (1 if new_pos > self._last_pos else -1) if (cards and self._last_pos and new_pos and new_pos != self._last_pos
                                                               and keys != self._keys) else 0
        self._last_pos = new_pos
        swiped = self._swiped
        self._nav = nav
        if keys != self._keys and cards:                      # замер: первая отрисовка — «отрисовка», при листании — «своп»
            self._pending_kind = 'swap' if direction else 'draw'
            self._m['swap'] = [None, None]
            if not direction:
                self._m['draw'] = [None, None]
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

        def first_frame(dt):                                  # первый экран: первый кусок построен и показан кадром
            if tok == self._tok and kind and self._m[kind][0] is None:
                self._m[kind][0] = (time.time() - t0) * 1000
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
        head = mlabel(c['header'], font_size=sp(14))
        key = c['key']
        box.add_widget(head)
        parts = []
        chunks = list(c['chunks'])

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


class BooksScreen(Screen):
    def __init__(self, app, **kw):
        super().__init__(name='books', **kw)
        self.app, self.ctl = app, app.ctl
        root = BoxLayout(orientation='vertical', spacing=dp(4), padding=dp(6))
        bar = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(4))
        bar.add_widget(btn('Добавить', self.add))
        bar.add_widget(icon_button(RefreshIcon(), self.ctl.refresh, dp(64), GRAY))
        bar.add_widget(btn('Индексировать', self.index_needed, color=GRAY))
        root.add_widget(bar)
        bar2 = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(4))
        bar2.add_widget(btn('Дневник', self.app.open_diary))
        bar2.add_widget(btn('Дополнение книги', self.app.open_append))
        root.add_widget(bar2)
        self.status = mlabel('', font_size=sp(13))
        self.status.color = ACCENT
        root.add_widget(self.status)
        self.progress = ProgressBar(max=100, value=0, size_hint_y=None, height=dp(6))
        root.add_widget(self.progress)
        self.sv, self.box = vlist()
        root.add_widget(self.sv)
        self.add_widget(root)
        self._rows = []

    def add(self):
        def got(paths):
            added = self.ctl.add_books(paths)
            if added:
                self.ctl.maybe_index(added)
        pick_files(lambda p: Clock.schedule_once(lambda dt: got(p), 0),
                   self.ctl.config.get('picker_start', ''),
                   lambda uri: self.ctl.set_setting('picker_start', uri),
                   self.ctl.paths.books_dir,
                   lambda t: Clock.schedule_once(lambda dt: self.ctl.on_status(t), 0))

    def on_pre_enter(self, *a):
        self.ctl.rescan()                    # вручную скопированные файлы видны сразу

    def index_needed(self):
        """Одна книга с устаревшим индексом — индексируем сразу; несколько — список с выбором."""
        need = [r for r in self._rows if r['state'] != 'ready']
        if not need:
            self.ctl.on_status('Все индексы актуальны')
        elif len(need) == 1:
            self.ctl.index_books([need[0]['file_name']])
        else:
            self.choose_to_index(need)

    def choose_to_index(self, need):
        box = BoxLayout(orientation='vertical', spacing=dp(4), padding=(dp(8), dp(6), dp(8), dp(6)))
        box.add_widget(mlabel('Книги с устаревшим или отсутствующим индексом:', color=(1, 1, 1, 1)))
        sv, lst = vlist()
        chosen = {r['file_name'] for r in need}
        boxes = {}
        lock = {'on': False}
        allcb = CheckBox(active=True, size_hint_x=None, width=dp(44))

        def row(cb, text):
            r_ = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(6))
            r_.add_widget(cb)
            r_.add_widget(mlabel(text, color=(1, 1, 1, 1)))
            lst.add_widget(r_)

        def on_all(w, v):
            if lock['on']:
                return
            lock['on'] = True
            for fn, cb in boxes.items():
                cb.active = v
            chosen.clear()
            if v:
                chosen.update(boxes)
            lock['on'] = False

        def on_one(fn, v):
            if lock['on']:
                return
            (chosen.add if v else chosen.discard)(fn)
            lock['on'] = True
            allcb.active = len(chosen) == len(boxes)
            lock['on'] = False
        row(allcb, 'Выбрать все')
        allcb.bind(active=on_all)
        for r in need:
            cb = CheckBox(active=True, size_hint_x=None, width=dp(44))
            cb.bind(active=lambda w, v, fn=r['file_name']: on_one(fn, v))
            boxes[r['file_name']] = cb
            row(cb, f"{escape_markup(r['display'])} · {r['size_kb']} КБ")
        box.add_widget(sv)
        pop = tall_popup('Индексация', box)
        bar = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(6))
        bar.add_widget(btn('Индексировать', lambda: (pop.dismiss(), self.ctl.index_books(
            [r['file_name'] for r in need if r['file_name'] in chosen]))))
        bar.add_widget(btn('Отмена', pop.dismiss, color=GRAY))
        box.add_widget(bar)
        pop.open()

    def set_status(self, text):
        self.status.text = escape_markup(text)

    def show_progress(self, name, done, total):
        pct = 100 * done / total if total else 0
        self.progress.value = pct
        self.status.text = escape_markup(f"Индексация: {name} — {pct:.0f}%")

    def show_books(self, rows):
        self._rows = rows

        def rebuild():
            self.box.clear_widgets()
            ready = [r for r in rows if r['state'] != 'not_indexed']
            if len(rows) > 1:                                  # «Выбрать все» / «Снять выбор»
                every = bool(ready) and all(r['selected'] for r in ready)
                self.box.add_widget(btn('Снять выбор' if every else 'Выбрать все',
                                        lambda: self.ctl.select_all(not every), color=GRAY))
            if not rows:
                self.box.add_widget(mlabel(
                    'Книг пока нет. Нажмите «Добавить» или положите .txt/.docx в папку книг приложения:\n'
                    + escape_markup(self.ctl.paths.books_dir), color=GRAY))
            for r in rows:
                self.box.add_widget(self._row(r))
        keep_scroll(self.sv, rebuild)

    def _row(self, r):
        row = BoxLayout(size_hint_y=None, height=dp(64), spacing=dp(6), padding=dp(4))
        cb = CheckBox(active=r['selected'], size_hint_x=None, width=dp(44), disabled=r['state'] == 'not_indexed')
        fn = r['file_name']
        cb.bind(active=lambda w, v, fn=fn: self.ctl.toggle_book(fn) if v != self.ctl.is_selected(fn) else None)
        info = mlabel(f"[b]{escape_markup(r['display'])}[/b]\n[size=12sp][color=777777]"
                      f"{escape_markup(r['status'])} · {r['separator']} · {r['size_kb']} КБ[/color][/size]")
        info.size_hint_y = 1
        info.bind(height=lambda *_: None)
        edit = icon_button(PencilIcon(), lambda r=r: self.edit(r), dp(56), BTN2)
        for w in (cb, info, edit):
            row.add_widget(w)
        return row

    def edit(self, r):
        """Название и разделитель — два поля; «Отмена» просто закрывает окно без изменений."""
        box = BoxLayout(orientation='vertical', spacing=dp(6), padding=(dp(8), dp(6), dp(8), dp(6)))
        name = no_bubble(TextInput(text=r['display'], hint_text='Название (алиас)', multiline=False,
                         size_hint_y=None, height=dp(44), font_size=sp(16)))
        sep = TextInput(text=r['separator'], hint_text='Разделитель: N1000, NS300, Numeric, ExcelRow1 или строка',
                        multiline=False, size_hint_y=None, height=dp(44), font_size=sp(16))
        no_bubble(sep)
        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        pop = top_popup(r['file_name'], box, dp(210))

        def save():
            pop.dismiss()
            new_sep = sep.text.strip() or r['separator']
            self.ctl.edit_book(r['file_name'], name.text.strip() or r['display'], new_sep)
            if new_sep != r['separator']:                      # индекс зависит только от разделителя
                self.ctl.maybe_index([r['file_name']])
        row.add_widget(btn('Сохранить', save))
        row.add_widget(btn('Отмена', pop.dismiss, color=GRAY))
        for w in (name, sep, row):
            box.add_widget(w)
        pop.open()


class RefreshIcon(Widget):
    """Круговая стрелка (обновить): дуга почти в полный круг со стрелкой на конце."""
    def __init__(self, **kw):
        super().__init__(**kw)
        self.bind(pos=self._draw, size=self._draw)

    def _draw(self, *a):
        import math
        from kivy.graphics import Line, Triangle
        self.canvas.clear()
        cx, cy = self.center_x, self.center_y
        r = min(self.width, self.height) * 0.26
        a0, a1 = 25, 320
        with self.canvas:
            Color(1, 1, 1, 1)
            Line(circle=(cx, cy, r, a0, a1), width=dp(2.2))
            t = math.radians(a1)
            ex, ey = cx + r * math.sin(t), cy + r * math.cos(t)           # конец дуги
            dx, dy = math.cos(t), -math.sin(t)                             # направление движения (по часовой)
            nx, ny = -dy, dx
            h, w = r * 0.75, r * 0.6
            Triangle(points=[ex + dx * h, ey + dy * h, ex + nx * w, ey + ny * w, ex - nx * w, ey - ny * w])


def icon_button(icon_widget, cb, width, bg):
    b = Button(size_hint_x=None, width=width, background_normal='', background_color=bg)
    icon_widget.size_hint = (None, None)
    b.add_widget(icon_widget)

    def place(*a):
        icon_widget.size = b.size
        icon_widget.pos = b.pos
    b.bind(pos=place, size=place)
    b.bind(on_release=lambda *_: cb())
    return b


class PencilIcon(Widget):
    """Пиктограмма «редактировать» (карандаш), цвет по теме."""
    def __init__(self, **kw):
        super().__init__(**kw)
        self.bind(pos=self._draw, size=self._draw)

    def _draw(self, *a):
        import math
        from kivy.graphics import Line, Quad, Triangle
        self.canvas.clear()
        s = min(self.width, self.height)
        cx, cy = self.center_x, self.center_y
        ax, ay = cx - 0.30 * s, cy - 0.30 * s                  # кончик
        bx, by = cx + 0.28 * s, cy + 0.28 * s                  # противоположный конец
        L = math.hypot(bx - ax, by - ay)
        ux, uy = (bx - ax) / L, (by - ay) / L
        nx, ny = -uy, ux
        w, tip = 0.085 * s, 0.17 * s
        with self.canvas:
            Color(*BTN2_TXT)
            Triangle(points=[ax, ay, ax + ux * tip + nx * w, ay + uy * tip + ny * w,
                             ax + ux * tip - nx * w, ay + uy * tip - ny * w])
            Quad(points=[ax + ux * (tip + dp(1.5)) + nx * w, ay + uy * (tip + dp(1.5)) + ny * w,
                         ax + ux * (tip + dp(1.5)) - nx * w, ay + uy * (tip + dp(1.5)) - ny * w,
                         bx - ux * 0.07 * s - nx * w, by - uy * 0.07 * s - ny * w,
                         bx - ux * 0.07 * s + nx * w, by - uy * 0.07 * s + ny * w])
            Line(points=[cx - 0.36 * s, cy - 0.40 * s, cx + 0.10 * s, cy - 0.40 * s], width=dp(1.6))


class ShareIcon(Widget):
    """Системная пиктограмма «поделиться»: три жирные точки, соединённые в виде «<»."""
    def __init__(self, **kw):
        super().__init__(**kw)
        self.bind(pos=self._draw, size=self._draw)

    def _draw(self, *a):
        from kivy.graphics import Ellipse, Line
        self.canvas.clear()
        cx, cy, s = self.center_x, self.center_y, min(self.width, self.height)
        r = s * 0.09
        pts = [(cx + s * 0.18, cy + s * 0.22), (cx - s * 0.18, cy), (cx + s * 0.18, cy - s * 0.22)]
        with self.canvas:
            Color(1, 1, 1, 1)
            Line(points=[pts[0][0], pts[0][1], pts[1][0], pts[1][1], pts[2][0], pts[2][1]], width=dp(1.6))
            for x, y in pts:
                Ellipse(pos=(x - r, y - r), size=(2 * r, 2 * r))


def divider():
    """Тонкая линия-разделитель между развёрнутыми «Настройками» и остальными пунктами."""
    w = Widget(size_hint_y=None, height=dp(12))

    def draw(*a):
        w.canvas.clear()
        with w.canvas:
            Color(*GRAY)
            Rectangle(pos=(w.x, w.center_y - dp(1)), size=(w.width, dp(2)))
    w.bind(pos=draw, size=draw)
    return w


def segmented(options, current, on_pick):
    """Ряд кнопок-переключателей (выбранная подсвечена): options = [(название, значение)]."""
    row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
    buttons = []

    def pick(value):
        for b, v in buttons:
            b.background_color = ACCENT if v == value else GRAY
        on_pick(value)
    for label, value in options:
        b = btn(label, lambda v=value: pick(v), color=ACCENT if value == current else GRAY)
        buttons.append((b, value))
        row.add_widget(b)
    return row


class ChevronButton(Button):
    """Кнопка-раскрывашка: справа нарисован крупный шеврон (вниз — закрыто, вверх — раскрыто)."""
    def __init__(self, text, **kw):
        super().__init__(text=text, **kw)
        self.opened = False
        self.bind(pos=self._draw, size=self._draw)

    def set_open(self, v):
        self.opened = v
        self._draw()

    def _draw(self, *a):
        from kivy.graphics import Line
        self.canvas.after.clear()
        s = dp(9)
        cx, cy = self.x + self.width - dp(30), self.y + self.height / 2.0     # не right/center_y: они обновляются позже size
        d = s / 2 if self.opened else -s / 2
        with self.canvas.after:
            Color(1, 1, 1, 1)
            Line(points=[cx - s, cy - d, cx, cy + d, cx + s, cy - d], width=dp(2.6), cap='round', joint='round')


class SettingsScreen(Screen):
    def __init__(self, app, **kw):
        super().__init__(name='settings', **kw)
        self.app, self.ctl = app, app.ctl
        col = BoxLayout(orientation='vertical', spacing=dp(6), padding=dp(10), size_hint_y=None)
        col.bind(minimum_height=col.setter('height'))
        font = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        self.font_label = mlabel(f"Размер шрифта: {self.ctl.config.get_int('font_size', 16)}")
        font.add_widget(self.font_label)
        font.add_widget(btn('−', lambda: self.font(-1), size_hint_x=None, width=dp(60)))
        font.add_widget(btn('+', lambda: self.font(1), size_hint_x=None, width=dp(60)))
        col.add_widget(font)
        theme = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        cur = self.ctl.config.get('theme', 'day')
        theme.add_widget(btn('Обычный вид', lambda: self.app.set_theme('day'),
                             color=ACCENT if cur != 'night' else GRAY))
        theme.add_widget(btn('Ночной вид', lambda: self.app.set_theme('night'),
                             color=ACCENT if cur == 'night' else GRAY))
        col.add_widget(theme)
        col.add_widget(divider())
        col.add_widget(btn('Экспорт', self.app.open_export, color=GRAY))
        col.add_widget(divider())
        self.set_btn = ChevronButton('Настройки', size_hint_y=None, height=dp(44), background_normal='',
                                     background_color=GRAY, color=(1, 1, 1, 1))
        self.set_btn.bind(on_release=lambda *_: self.toggle_settings())
        col.add_widget(self.set_btn)
        self.set_box = BoxLayout(orientation='vertical', size_hint_y=None, height=0, spacing=dp(6))
        self.set_box.bind(minimum_height=self.set_box.setter('height'))      # высота по содержимому: надписи не наезжают на кнопки
        from kivy.graphics import Rectangle as _R
        with self.set_box.canvas.before:
            Color(*PANEL_BG)
            self._panel = _R()
            Color(*ACCENT)
            self._bar = _R()

        def lay(*_):
            self._panel.pos, self._panel.size = self.set_box.pos, self.set_box.size
            self._bar.pos, self._bar.size = self.set_box.pos, (dp(4) if self.set_box.children else 0, self.set_box.height)
        self.set_box.bind(pos=lay, size=lay)
        col.add_widget(self.set_box)
        col.add_widget(btn('О программе', self.app.open_about, color=GRAY))
        sv = ScrollView()
        sv.add_widget(col)
        self.add_widget(sv)

    def toggle_settings(self):
        """Скрытые элементы не лежат в разметке (иначе перехватывали бы нажатия)."""
        if self.set_box.children:
            self.set_box.clear_widgets()
            self.set_box.padding = 0
            self.set_box.height = 0
            self.set_btn.set_open(False)
            self.set_btn.background_color = GRAY
            return
        self.set_box.padding = (dp(12), dp(8), dp(8), dp(8))
        self.set_box.add_widget(mlabel('Текст страниц'))
        self.set_box.add_widget(segmented(
            [('Полный', 0), ('Краткий', 1), ('Отметки', 2)],
            2 if self.ctl.marks_only() else (1 if self.ctl.config.get_bool('brief_view', False) else 0),
            lambda v: (self.ctl.set_setting('brief_view', 1 if v == 1 else 0), self.ctl.set_setting('marks_view', 1 if v == 2 else 0),
                       self.ctl.reset_views(), self.app.refresh_actions(),
                       self.ctl.on_status("Вид текста изменён: повторите поиск или откройте «Чтение»"))))
        step = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        self._step_label = mlabel(f"Шрифт отметок: +{self.ctl.font_step()}")
        step.add_widget(self._step_label)
        step.add_widget(btn('−', lambda: self.step(-1), size_hint_x=None, width=dp(60)))
        step.add_widget(btn('+', lambda: self.step(1), size_hint_x=None, width=dp(60)))
        self.set_box.add_widget(step)
        sw = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        self._swap_label = mlabel(f"Листание не быстрее: {self.ctl.swap_min_ms()} мс")
        sw.add_widget(self._swap_label)
        sw.add_widget(btn('−', lambda: self.swap_floor(-50), size_hint_x=None, width=dp(60)))
        sw.add_widget(btn('+', lambda: self.swap_floor(50), size_hint_x=None, width=dp(60)))
        self.set_box.add_widget(sw)
        self.set_box.add_widget(mlabel('Индексация'))
        self.set_box.add_widget(segmented(
            [('Ручная', 0), ('Автоматическая', 1)], 1 if self.ctl.auto_index_on() else 0,
            lambda v: (self.ctl.set_setting('index_auto', v), self.ctl.rescan() if v else None)))
        self._extra = 0
        extra_btn = 0
        if safsync.available():
            uri = self.ctl.sync_uri()
            name = ''
            if uri:
                try:
                    name = safsync.SafFolder(uri).label()
                except Exception:
                    name = '?'
            self.set_box.add_widget(btn('Папка синхронизации' + (f': {name}' if name else '…'), self.pick_sync, color=GRAY))
            self.set_box.add_widget(btn('Синхронизировать', self.ctl.sync_now, color=ACCENT if uri else GRAY))
            self.set_box.add_widget(btn('Архив папки синхронизации в «Загрузки»', self.app.archive_now,
                                        color=ACCENT if uri else GRAY))
            extra_btn = 3
        self.set_box.add_widget(btn('Пароль', self.app.open_password, color=GRAY))
        self.set_btn.set_open(True)
        self.set_btn.background_color = ACCENT

    def step(self, delta):
        v = min(10, max(1, self.ctl.font_step() + delta))
        self.ctl.set_setting('font_step', v)
        self._step_label.text = f"Шрифт отметок: +{v}"
        self.ctl.reset_views()
        self.ctl.on_status('Шаг шрифта изменён: повторите поиск или откройте «Чтение»')

    def swap_floor(self, delta):
        v = min(1000, max(0, self.ctl.swap_min_ms() + delta))
        self.ctl.set_setting('swap_min_ms', v)
        self._swap_label.text = f"Листание не быстрее: {v} мс"

    def pick_sync(self):
        def got(uri):
            self.ctl.set_setting('sync_uri', uri)
            self.ctl.on_status("Папка синхронизации выбрана. Нажмите «Синхронизировать».")
            if self.set_box.children:                      # обновить подпись кнопки
                self.toggle_settings()
                self.toggle_settings()
        safsync.pick_folder(got, self.ctl.on_status)

    def font(self, delta):
        self.font_label.text = f"Размер шрифта: {self.app.change_font(delta)}"

    def add_shortcut(self):
        add_home_shortcut(self.ctl.on_status)


# ===================================================================
class SinevaApp(App):
    title = 'Синева Mobile'

    def build(self):
        """Сначала показывается заставка (слово собирается из пикселей), интерфейс создаётся за ней."""
        self.ready_root = None
        self._sync_pop = None
        self.splash = Splash(on_done=self._swap)
        Clock.schedule_once(self._init_app, 0.3)
        return self.splash

    def _swap(self):
        """Заставка закончилась и интерфейс готов — показываем приложение."""
        if getattr(self, 'ready_root', None) is None or not self.splash.finished or self.root is not self.splash:
            return
        Window.remove_widget(self.splash)
        Window.add_widget(self.ready_root)
        self.root = self.ready_root
        Window.clearcolor = BG
        if self.ctl.has_password():
            self.ask_password()

    def _init_app(self, dt):
        """Запуск по шагам, по одному на кадр: заставка не замирает, пока строится интерфейс."""
        steps = [self._init_core, self._init_ui, self._init_finish]

        def run(dt2=0):
            steps.pop(0)()
            if steps:
                Clock.schedule_once(run, 0.05)
        run()

    def _init_core(self):
        self.ctl = Controller(ui_post=ui_post, schedule=schedule)
        apply_theme(self.ctl.config.get('theme', 'day'))

    def _init_ui(self):
        self._root_tmp = self._make_root()

    def _init_finish(self):
        root = self._root_tmp
        Window.bind(on_keyboard=self._on_key)
        Window.bind(on_request_close=lambda *a: (self.confirm_exit(), True)[1])
        try:
            Window.softinput_mode = 'below_target'
        except Exception:
            pass
        self.ctl.publish_books()
        self.sm.current = 'books'                           # приложение всегда стартует с вкладки «Книги»
        self.ready_root = root
        self._swap()

    def set_theme(self, name):
        """Обычная/ночная тема: цвета меняются, интерфейс пересоздаётся (вкладка и запрос сохраняются)."""
        if name == self.ctl.config.get('theme', 'day'):
            return
        self.ctl.set_setting('theme', name)
        apply_theme(name)
        cur, query = self.sm.current, self.search_s.input.text
        old = self.root
        for scr in (self.search_s, self.read_s):
            scr.destroy()
        new = self._make_root()
        Window.remove_widget(old)
        Window.add_widget(new)
        self.root = self.ready_root = new
        self.search_s.input.text = query
        self.sm.current = cur
        self.ctl.publish_books()
        self.ctl.reset_views()
        self.ctl.on_status("Вид изменён: повторите поиск или откройте «Чтение»")

    def _make_root(self):
        Window.clearcolor = BG
        self.sm = ScreenManager(transition=NoTransition())
        self.search_s = SearchScreen(self)
        self.books_s = BooksScreen(self)
        self.settings_s = SettingsScreen(self)
        self.read_s = SearchScreen(self, reading=True)
        for s in (self.search_s, self.read_s, self.books_s, self.settings_s):
            self.sm.add_widget(s)
        c = self.ctl
        c.on_cards = lambda *a: (self.read_s if c.tab == 'read' else self.search_s).show_cards(*a)
        c.on_status = lambda t: (self.search_s.set_status(t), self.read_s.set_status(t), self.books_s.set_status(t))
        c.on_books = self.books_s.show_books
        c.on_progress = self.books_s.show_progress
        c.on_reset = lambda: (self.search_s.clear(), self.read_s.clear())
        c.on_undo = self._on_undo_count
        c.on_sync = self.sync_dialog
        if is_android():
            c.save_download = save_to_downloads
        c.on_ask_continue = self.ask_continue

        root = BoxLayout(orientation='vertical')
        root.add_widget(self.sm)
        nav = BoxLayout(size_hint_y=None, height=dp(52))
        self.nav_buttons = {}
        for label, name in (('Поиск', 'search'), ('Чтение', 'read'), ('Книги', 'books'), ('Ещё', 'settings')):
            b = Button(text=label, background_normal='', background_color=NAV, color=NAV_TXT, font_size=sp(18))
            b.bind(on_release=lambda *_a, n=name: self.go_tab(n))
            self.nav_buttons[name] = b
            nav.add_widget(b)
        self.sm.bind(current=lambda w, v: self._mark_tab(v))
        self._mark_tab(self.sm.current)
        root.add_widget(nav)
        return root

    def _mark_tab(self, name):
        """Активная вкладка выделяется цветом."""
        for n, b in self.nav_buttons.items():
            b.background_color = ACCENT if n == name else NAV
            b.color = (1, 1, 1, 1) if n == name else NAV_TXT

    # ---------- слои текста: подчёркивание, «+Шрифт», «+Цвет», «+Мысли» ----------
    cur_sel = None                  # последнее выделение: {'key', 's', 'e'} — в координатах текста карточки
    _undo_n = 0

    def _on_undo_count(self, n):
        self._undo_n = n
        self.refresh_actions()

    def refresh_actions(self):
        """Кнопки «Отмена / +Шрифт / +Цвет / +Мысли» видны, пока есть что отменять или выделено слово."""
        on = (self._undo_n > 0 or self.cur_sel is not None) and not self.ctl.marks_only()
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

    def go_tab(self, name):
        if self.sm.current != name:
            self._prev_tab = self.sm.current if self.sm.current != 'read' else getattr(self, '_prev_tab', 'search')
        self.sm.current = name                              # экран открывается сразу,
        if name in ('search', 'read'):                      # а содержимое готовится уже после его отрисовки
            Clock.schedule_once(lambda dt: self.ctl.set_tab(name), 0)

    def on_resume(self):
        if getattr(self, 'ctl', None) is None or self.ready_root is None:
            return True
        self.ctl.rescan()
        return True

    def open_about(self):
        box = BoxLayout(orientation='vertical', spacing=dp(8), padding=dp(8))
        sizes = self.ctl.folder_sizes()
        mb = lambda n: f"{n / 1048576:.1f}".replace('.', ',') + ' МБ'
        box.add_widget(Label(
            text=(f"Синева Mobile {__version__}\n\nПапка книг: {mb(sizes['books'])}\n{self.ctl.paths.books_dir}\n\n"
                  f"Папка индексов: {mb(sizes['index'])}\n{self.ctl.paths.index_dir}\n\n"
                  f"Конфигурация:\n{self.ctl.paths.config_path}"),
            font_size=sp(14), halign='left', valign='top', color=(1, 1, 1, 1),
            text_size=(Window.width - dp(60), None)))
        pop = top_popup('О программе', box, dp(470))
        box.add_widget(btn('Закрыть', pop.dismiss, color=GRAY))
        pop.open()

    def open_password(self):
        """Создание, смена или удаление пароля (запрашивается при запуске после заставки)."""
        ctl = self.ctl
        has = ctl.has_password()
        box = BoxLayout(orientation='vertical', spacing=dp(6), padding=(dp(8), dp(6), dp(8), dp(6)))

        def field(hint):
            f = no_bubble(TextInput(hint_text=hint, multiline=False, password=True, size_hint_y=None,
                                    height=dp(44), font_size=sp(16)))
            box.add_widget(f)
            return f
        old = field('Текущий пароль') if has else None
        new = field('Новый пароль' + (' (пусто — убрать пароль)' if has else ''))
        again = field('Повторите новый пароль')
        msg = Label(text='', color=(1, 0.6, 0.6, 1), font_size=sp(14), size_hint_y=None, height=dp(24))
        box.add_widget(msg)
        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        pop = top_popup('Пароль', box, dp(60) + dp(44) * (4 if has else 3) + dp(80))

        def save():
            if has and not ctl.check_password(old.text):
                msg.text = 'Неверный текущий пароль'
                return
            if new.text != again.text:
                msg.text = 'Пароли не совпадают'
                return
            if not has and not new.text:
                msg.text = 'Введите пароль'
                return
            ctl.set_password(new.text)
            ctl.on_status('Пароль установлен' if new.text else 'Пароль убран')
            pop.dismiss()
        row.add_widget(btn('Сохранить', save))
        row.add_widget(btn('Отмена', pop.dismiss, color=GRAY))
        box.add_widget(row)
        pop.open()

    def ask_password(self):
        """Вход по паролю после заставки: окно закрывает всё приложение, пока не введён верный пароль."""
        self._locked = True
        box = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(24))
        box.add_widget(Label(text='Синева Mobile', font_size=sp(26), size_hint_y=None, height=dp(60)))
        field = no_bubble(TextInput(hint_text='Пароль', multiline=False, password=True, size_hint_y=None,
                                    height=dp(48), font_size=sp(20)))
        box.add_widget(field)
        msg = Label(text='', color=(1, 0.6, 0.6, 1), size_hint_y=None, height=dp(28))
        box.add_widget(msg)
        pop = style_popup(Popup(title='', content=box, size_hint=(1, 1), auto_dismiss=False, separator_height=0))

        def enter(*_):
            if self.ctl.check_password(field.text):
                self._locked = False
                pop.dismiss()
            else:
                msg.text = 'Неверный пароль'
                field.text = ''
        box.add_widget(btn('Войти', enter))
        box.add_widget(BoxLayout())
        field.bind(on_text_validate=enter)
        pop.open()

    def confirm_exit(self):
        if getattr(self, '_exit_pop', None) is not None:
            return
        box = BoxLayout(orientation='vertical', spacing=dp(8), padding=dp(8))
        box.add_widget(Label(text='Выйти из программы?', font_size=sp(18)))
        row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(6))
        pop = top_popup('Выход', box, dp(180))
        self._exit_pop = pop
        pop.bind(on_dismiss=lambda *a: setattr(self, '_exit_pop', None))
        row.add_widget(btn('Выйти', lambda: (pop.dismiss(), self.stop())))
        row.add_widget(btn('Остаться', pop.dismiss, color=GRAY))
        box.add_widget(row)
        pop.open()

    def sync_dialog(self, state, text):
        """Окно «Синхронизация»: пока идёт обмен — крутится индикатор, закрыть нельзя; по окончании — итог,
           предложение сделать архив папки в «Загрузки» и кнопка «Готово».
           Состояния: start / done (синхронизация завершена) / error / backup (итог архивации)."""
        pop = self._sync_pop
        if state == 'start':
            if pop is not None:                              # окно уже открыто (архив после синхронизации)
                lab, bar, ok, arc = self._sync_parts
                lab.text = text
                ok.disabled = arc.disabled = True
                arc.opacity = 0
                self._sync_anim.cancel()
                self._sync_anim = Clock.schedule_interval(lambda dt: setattr(bar, 'value', (bar.value + 6) % 100), 0.1)
                return
            box = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(10))
            lab = Label(text=text, font_size=sp(16), halign='center', valign='middle')
            lab.bind(size=lambda w, v: setattr(w, 'text_size', (v[0], None)))
            bar = ProgressBar(max=100, value=0, size_hint_y=None, height=dp(8))
            arc = btn('Архив папки в «Загрузки»', self.archive_now, color=ACCENT)
            arc.disabled, arc.opacity = True, 0
            ok = btn('Готово', lambda: self._sync_close(), color=GRAY)
            ok.disabled = True
            for w in (lab, bar, arc, ok):
                box.add_widget(w)
            pop = top_popup('Синхронизация', box, dp(300))
            pop.open()
            self._sync_pop, self._sync_parts = pop, (lab, bar, ok, arc)
            self._sync_anim = Clock.schedule_interval(lambda dt: setattr(bar, 'value', (bar.value + 6) % 100), 0.1)
            return
        if pop is None:
            return
        lab, bar, ok, arc = self._sync_parts
        self._sync_anim.cancel()
        bar.value = 100 if state in ('done', 'backup') else 0
        lab.text = text + ("\n\nМожно сохранить архив папки синхронизации для аварийного восстановления." if state == 'done' and self.ctl.sync_uri() else '')
        ok.disabled = False
        ok.background_color = ACCENT
        offer = state == 'done' and bool(self.ctl.sync_uri())
        arc.disabled, arc.opacity = not offer, 1 if offer else 0

    def archive_now(self):
        """Архив папки синхронизации -> «Загрузки» (не пропадает при переустановке)."""
        if self.ctl.config.get('sync_uri', '') in ('', None):
            self.ctl.on_status('Сначала выберите папку синхронизации')
            return
        self.sync_dialog('start', 'Создаётся архив папки синхронизации…\nПодождите.')
        self.ctl.backup_sync_folder(lambda ok, t: self.sync_dialog('backup' if ok else 'error', t))

    def _sync_close(self):
        pop, self._sync_pop = self._sync_pop, None
        if pop is not None:
            pop.dismiss()

    def ask_continue(self, book, history, cont, goto, total):
        """Окно чтения: первые предложения последних прочитанных страниц (последняя — снизу); «Продолжить» — с выбранной;
           «Страница» — переход на любую страницу 1…total."""
        box = BoxLayout(orientation='vertical', spacing=dp(6), padding=dp(8))
        state = {'sel': history[-1][0] if history else None}
        listing = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(4))
        listing.bind(minimum_height=listing.setter('height'))
        sv = ScrollView()
        sv.add_widget(listing)

        def fill():
            listing.clear_widgets()
            for rowid, number, sentence in history:
                chosen = rowid == state['sel']
                b = Button(text=f"[b]{number}[/b]  {escape_markup(sentence)}", markup=True, halign='left', valign='middle',
                           size_hint_y=None, height=dp(56), font_size=sp(14), background_normal='', color=(1, 1, 1, 1),
                           background_color=ACCENT if chosen else (0.3, 0.32, 0.4, 1))
                b.bind(size=lambda w, v: setattr(w, 'text_size', (v[0] - dp(12), v[1])))
                b.bind(on_release=lambda *_a, r=rowid: (state.__setitem__('sel', r), fill()))
                listing.add_widget(b)
        fill()
        box.add_widget(mlabel(f"«{escape_markup(book)}»: с какой страницы продолжить?", color=(1, 1, 1, 1), font_size=sp(14)))
        box.add_widget(sv)
        row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(6))
        pop = top_popup('Режим чтения', box, min(Window.height * 0.8, dp(150) + dp(60) * max(1, len(history))))
        row.add_widget(btn('Продолжить', lambda: (pop.dismiss(), cont(state['sel']))))

        def ask_page():
            inp = TextInput(text='1', multiline=False, input_filter='int', size_hint_x=0.34, font_size=sp(18),
                            halign='center')
            line = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
            line.add_widget(mlabel('Страница', color=(1, 1, 1, 1), font_size=sp(16)))
            line.add_widget(inp)
            line.add_widget(mlabel(f"из {total}", color=(1, 1, 1, 1), font_size=sp(16)))
            b2 = BoxLayout(orientation='vertical', spacing=dp(8), padding=dp(8))
            b2.add_widget(line)
            r2 = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
            p2 = top_popup('Перейти на страницу', b2, dp(190))

            def go_page(*_a):
                try:
                    n = int(inp.text or 1)
                except ValueError:
                    n = 1
                n = max(1, min(total, n))
                p2.dismiss()
                pop.dismiss()
                goto(n)
            r2.add_widget(btn('Перейти', go_page))
            r2.add_widget(btn('Отмена', lambda: p2.dismiss(), color=GRAY))
            b2.add_widget(r2)
            inp.bind(on_text_validate=go_page)
            p2.open()
            inp.focus = True
            Clock.schedule_once(lambda dt: inp.select_all(), 0.2)
        row.add_widget(btn('Страница', ask_page, color=GRAY))
        row.add_widget(btn('Отмена', lambda: (pop.dismiss(), self.go_tab(getattr(self, '_prev_tab', 'search') or 'search')),
                           color=GRAY))
        box.add_widget(row)
        pop.open()
        Clock.schedule_once(lambda dt: setattr(sv, 'scroll_y', 0), 0.2)          # последний блок виден снизу

    # ---------- размер шрифта ----------
    def change_font_live(self, delta):
        """Размер шрифта из окна статуса: страница на экране перерисовывается сразу, результаты не сбрасываются."""
        size = max(10, min(40, self.ctl.config.get_int('font_size', 16) + delta))
        self.ctl.set_setting('font_size', size)
        self.ctl.apply_font()
        return size

    def change_font(self, delta):
        size = max(10, min(40, self.ctl.config.get_int('font_size', 16) + delta))
        self.ctl.set_setting('font_size', size)
        self.ctl.reset_views()
        self.ctl.on_status(f"Шрифт {size}: повторите поиск или откройте «Чтение»")
        return size

    # ---------- «Дополнение книги» ----------
    def open_append(self):
        ctl = self.ctl
        books = ctl.library.books
        if not books:
            ctl.on_status('Сначала добавьте книги')
            return
        names = {f"{b.display} · {b.file_name}": b.file_name for b in books}
        remembered = ctl.append_target()
        first = next((k for k, v in names.items() if v == remembered), next(iter(names)))
        box = BoxLayout(orientation='vertical', spacing=dp(4), padding=(dp(8), dp(6), dp(8), dp(6)))
        spin = Spinner(text=first, values=list(names), size_hint_y=None, height=dp(40), font_size=sp(14),
                       background_normal='', background_color=BTN2, color=BTN2_TXT)
        last = ReadBox(to_end=False, size_hint_y=0.4, font_pt=ctl.config.get_int('font_size', 16))
        text = TextInput(hint_text='Добавляемый текст (в конец выбранной книги)', multiline=True,
                         size_hint_y=0.6, font_size=sp(ctl.config.get_int('font_size', 16)))
        no_bubble(text)
        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        pop = tall_popup('Дополнение книги', box)

        def show_last(*_):
            last.text = 'Загрузка…'
            def work(dt):
                last.text = ctl.last_block(names[spin.text]) or '(книга пуста)'
            Clock.schedule_once(work, 0.05)
        spin.bind(text=show_last)

        def paste():
            text.text = text.text + (Clipboard.paste() or '')

        def add():
            if not text.text.strip():
                return
            if self.ctl.append_to_book(names[spin.text], text.text):
                text.text = ''                              # окно остаётся открытым: можно дописывать дальше
                show_last()

        row.add_widget(btn('Добавить', add))
        row.add_widget(btn('CTR+V', paste, color=GRAY))
        row.add_widget(btn('Выход', pop.dismiss, color=GRAY))
        for w in (spin, last, row, text):                   # кнопки между окнами — клавиатура их не закроет
            box.add_widget(w)
        pop.open()
        show_last()

    def open_export(self):
        """Окно «Экспорт» на весь экран: что экспортировать + список ранее экспортированных файлов."""
        box = BoxLayout(orientation='vertical', spacing=dp(6), padding=(dp(8), dp(6), dp(8), dp(6)))
        pop = style_popup(Popup(title='Экспорт', content=box, size_hint=(1, 1), auto_dismiss=False, title_size=sp(16)))
        state = {'sel': None}
        listing = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(4))
        listing.bind(minimum_height=listing.setter('height'))

        def fill():
            listing.clear_widgets()
            items = self.ctl.list_exports()
            if not items:
                listing.add_widget(mlabel('Экспортированных файлов пока нет', color=(1, 1, 1, 1)))
            for name, path, mtime, size in items:
                row = BoxLayout(size_hint_y=None, height=dp(56), spacing=dp(4))
                chosen = state['sel'] == path
                b = Button(text=escape_markup(name) + f"\n[size={int(sp(12))}]{max(1, size // 1024)} КБ[/size]",
                           markup=True, halign='left', valign='middle', font_size=sp(15),
                           background_normal='', color=(1, 1, 1, 1),
                           background_color=ACCENT if chosen else (0.3, 0.32, 0.4, 1))
                b.bind(size=lambda w, v: setattr(w, 'text_size', (v[0] - dp(12), v[1])))
                b.bind(on_release=lambda *_a, p=path: (state.__setitem__('sel', p), fill()))
                ic = Button(size_hint_x=None, width=dp(56), background_normal='', background_color=GRAY)
                ic.add_widget(ShareIcon(size_hint=(None, None), size=(dp(56), dp(56))))
                ic.bind(on_release=lambda *_a, p=path: share_file(p, self.ctl.on_status))
                ic.children[0].pos = ic.pos
                ic.bind(pos=lambda w, v: setattr(w.children[0], 'pos', v))
                row.add_widget(b)
                row.add_widget(ic)
                listing.add_widget(row)

        def do(fn):
            path = fn()
            if path:
                state['sel'] = path
                fill()
                try:
                    shown = publish_shared(path)[1]
                    self.ctl.on_status(f"Сохранено: {shown}  (внутренняя копия: {path})")
                except Exception:
                    self.ctl.on_status(f"Сохранено: {path}")

        box.add_widget(mlabel('Что экспортировать:', color=(1, 1, 1, 1)))
        grid = BoxLayout(orientation='vertical', size_hint_y=None, height=dp(5 * 46), spacing=dp(6))
        for label, fn in (('Подчёркнутое (TXT)', lambda: self.ctl.export_marks('txt')),
                          ('Подчёркнутое (Word, DOCX)', lambda: self.ctl.export_marks('docx')),
                          ('Конспект', self.ctl.export_conspect),
                          ('Дневник', self.ctl.export_diary),
                          ('Мои мысли', self.ctl.export_notes)):
            grid.add_widget(btn(label, lambda fn=fn: do(fn)))
        box.add_widget(grid)
        box.add_widget(mlabel('Экспортированные файлы (последние сверху). Значок справа — отправить '
                              'в мессенджер, почту и т. д.:', font_size=sp(13), color=(1, 1, 1, 1)))
        sv = ScrollView()
        sv.add_widget(listing)
        box.add_widget(sv)
        box.add_widget(btn('Закрыть', pop.dismiss, color=GRAY))
        fill()
        pop.open()

    def open_diary(self):
        """Две части: сверху конец дневника (последние записи), снизу поле новой записи."""
        box = BoxLayout(orientation='vertical', spacing=dp(4), padding=(dp(8), dp(6), dp(8), dp(6)))
        last = ReadBox(to_end=True, size_hint_y=0.4, font_pt=self.ctl.config.get_int('font_size', 16))
        text = TextInput(hint_text='Запись добавится в конец файла dnev.txt', multiline=True,
                         size_hint_y=0.6, font_size=sp(self.ctl.config.get_int('font_size', 16)))
        no_bubble(text)
        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        pop = tall_popup('Дневник', box)

        def show_tail(dt=None):
            last.text = self.ctl.diary_tail() or '(дневник пока пуст)'

        def paste():
            text.text = text.text + (Clipboard.paste() or '')

        def add():
            if self.ctl.add_to_diary(text.text):
                text.text = ''                              # окно остаётся открытым
                show_tail()

        row.add_widget(btn('Записать', add))
        row.add_widget(btn('CTR+V', paste, color=GRAY))
        row.add_widget(btn('Выход', pop.dismiss, color=GRAY))
        for w in (last, row, text):
            box.add_widget(w)
        pop.open()
        show_tail()

    def _on_key(self, window, key, *a):
        if key == 27 and self._sync_pop is not None:
            return True                                     # во время и сразу после синхронизации «Назад» не действует
        if key == 27:                                       # «Назад»
            if getattr(self, '_locked', False):
                return True
            if getattr(self, 'ready_root', None) is None:
                return True
            if getattr(self, '_exit_pop', None) is not None:
                self._exit_pop.dismiss()
            elif self.sm.current != 'search':
                self.go_tab('search')
            else:
                self.confirm_exit()
            return True
        return False
