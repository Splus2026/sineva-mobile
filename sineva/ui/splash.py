"""Заставка «Синева Mobile»: логотип и слово, собирающееся из пикселей клубами дыма, пока приложение загружается."""
import os

from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle
from kivy.metrics import sp
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from .smoke import SmokeText

LOGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logo.png')
BG = (0.078, 0.102, 0.18, 1)               # тот же цвет, что у статичной заставки Android (presplash.png)


class Splash(Widget):
    HOLD = 0.7                              # сколько слово держится собранным

    def __init__(self, on_done=None, **kw):
        super().__init__(**kw)
        self.on_done = on_done
        self.smoke = SmokeText()
        self.t = 0.0
        self.finished = False
        Window.clearcolor = BG
        with self.canvas:
            Color(*BG)
            self.bg = Rectangle(pos=self.pos, size=self.size)
            self.logo_color = Color(1, 1, 1, 0)
            self.logo = Rectangle(source=LOGO, pos=self.pos, size=(1, 1))
            self.dots = []
            n = len(self.smoke)
            for i in range(n):
                k = self.smoke.p[i][0] / max(1, self.smoke.cols)
                col = Color(0.55 - 0.2 * k, 0.75 + 0.15 * k, 1.0, 0)
                self.dots.append((col, Rectangle(pos=(0, 0), size=(0, 0))))
        self.wait = Label(text='Загрузка', font_size=sp(18), color=(0.67, 0.8, 1, 1), size_hint=(None, None))
        self.add_widget(self.wait)
        self.bind(pos=self._layout, size=self._layout)
        self._layout()
        self._ev = Clock.schedule_interval(self._tick, 1 / 30.0)

    def _layout(self, *a):
        w, h = self.size
        self.bg.pos, self.bg.size = self.pos, self.size
        self.cell = max(1.0, (w * 0.86) / (self.smoke.cols + 1))
        self.x0 = self.x + (w - self.smoke.cols * self.cell) / 2
        self.y_top = self.y + h * 0.34
        side = min(w, h) * 0.46
        self.logo.size = (side, side)
        self.logo.pos = (self.x + (w - side) / 2, self.y + h * 0.52)
        self.wait.size = (w, sp(30))
        self.wait.pos = (self.x, self.y + h * 0.1)

    def _tick(self, dt):
        self.t += min(dt, 0.1)
        t = self.t
        self.wait.text = 'Загрузка' + '.' * (1 + int(t / 0.45) % 3)      # точки добавляются: . .. ...
        self.wait.opacity = 0.0 if self.finished else 1.0
        self.logo_color.a = min(1.0, max(0.0, (t - 0.3) / 1.6))      # круг проявляется вместе со словом
        size0 = self.cell / self.smoke.sub
        for i, (col, rect) in enumerate(self.dots):
            x, y, sc, al = self.smoke.state(i, t)
            s = size0 * sc
            col.a = al
            rect.size = (s, s)
            rect.pos = (self.x0 + x * self.cell - s / 2, self.y_top - y * self.cell - s / 2)
        if not self.finished and t >= self.smoke.assembled_at + self.HOLD:
            self.finished = True
            self._ev.cancel()
            if self.on_done:
                self.on_done()
