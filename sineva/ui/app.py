"""Приложение «Синева Mobile» на Kivy: сборка экранов, вкладки, диалоги. Вся логика — в sineva.controller.Controller."""
from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.screenmanager import NoTransition, ScreenManager
from ..controller import Controller
from .picker import save_to_downloads, is_android
from .splash import Splash
from .theme import apply_theme, register
from .search_screen import SearchScreen
from .books_screen import BooksScreen
from .settings_screen import SettingsScreen
from .widgets import schedule, ui_post

from .app_layers import LayersMixin
from .app_dialogs import DialogsMixin

register(globals())                      # цвета темы лежат обычными именами модуля


class SinevaApp(LayersMixin, DialogsMixin, App):
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

    def go_tab(self, name):
        if name != 'search':
            self.ctl.reset_filters()                        # фильтр по словам живёт только внутри поиска
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
