"""Общие виджеты и вспомогательные функции интерфейса (окна, метки, выделяемый текст, значки)."""
import math
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color
from kivy.graphics import PopMatrix
from kivy.graphics import PushMatrix
from kivy.graphics import Rectangle
from kivy.graphics import Translate
from kivy.metrics import dp
from kivy.metrics import sp
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.properties import NumericProperty
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget
from .theme import SEL_BLUE, YELLOW, YELLOW_SOFT, register
from .textmodel import atom_at, build_markup, note_at_point

register(globals())                      # цвета темы лежат обычными именами модуля


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


class TapCell(AnchorLayout):
    """Ячейка шапки: нажатие в любом месте ячейки (а не только по буквам) вызывает on_tap — крупная цель для пальца."""
    on_tap = None

    def on_touch_down(self, touch):
        if self.on_tap is not None and self.collide_point(*touch.pos):
            self.on_tap()
            return True
        return super().on_touch_down(touch)


class Track(FloatLayout):
    """Лента страниц: сдвигается матрицей отрисовки (Translate) без пересчёта раскладки — листание идёт на частоте кадров."""
    tx = NumericProperty(0)

    def __init__(self, **kw):
        super().__init__(**kw)
        with self.canvas.before:
            PushMatrix()
            self._tr = Translate(0, 0)
        with self.canvas.after:
            PopMatrix()
        self.bind(tx=lambda w, v: setattr(self._tr, 'x', v))


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
