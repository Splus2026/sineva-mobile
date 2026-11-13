"""Жесты и листание: перетаскивание страницы, соседние панели, анимация."""
import math
import time
from kivy.animation import Animation
from kivy.clock import Clock
from kivy.graphics import Color, Rectangle
from kivy.metrics import dp
from kivy.uix.widget import Widget
from .widgets import vlist


class GestureMixin:
    """Жесты и листание: перетаскивание страницы, соседние панели, анимация. Методы смешиваются в SearchScreen."""

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
