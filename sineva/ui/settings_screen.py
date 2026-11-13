"""Экран «Ещё» (настройки)."""
from kivy.graphics import Color
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.screenmanager import Screen
from kivy.uix.scrollview import ScrollView
from .picker import add_home_shortcut
from . import safsync
from .theme import register
from .widgets import ChevronButton, btn, divider, mlabel, segmented

register(globals())                      # цвета темы лежат обычными именами модуля


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
        self.set_box.add_widget(mlabel('Статус'))
        self.set_box.add_widget(segmented(
            [('Сокращённый', 1), ('Полный', 0)], 1 if self.ctl.short_status() else 0, self.set_short_status))
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

    def set_short_status(self, v):
        self.ctl.set_setting('short_status', int(v))
        for scr in (self.app.search_s, self.app.read_s):
            scr.apply_status_mode()
        self.ctl.restate()

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
