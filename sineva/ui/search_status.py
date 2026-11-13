"""Окно статуса: сообщения, время и размеры отрисовок."""
from kivy.metrics import dp, sp
from kivy.uix.label import Label
from ..search import escape_markup


class StatusMixin:
    """Окно статуса: сообщения, время и размеры отрисовок. Методы смешиваются в SearchScreen."""

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
