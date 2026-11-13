"""Приложение «Синева Mobile» на Kivy: сборка экранов, вкладки, диалоги. Вся логика — в sineva.controller.Controller."""
from kivy.app import App
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.metrics import sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.progressbar import ProgressBar
from kivy.uix.screenmanager import NoTransition
from kivy.uix.screenmanager import ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from .. import __version__
from ..controller import Controller
from ..search import escape_markup
from .picker import publish_shared
from .picker import share_file
from .picker import save_to_downloads
from .picker import is_android
from .splash import Splash
from .theme import apply_theme, register
from .search_screen import SearchScreen
from .books_screen import BooksScreen
from .settings_screen import SettingsScreen
from .widgets import PencilIcon, ReadBox, ShareIcon, btn, icon_button, mlabel, no_bubble, schedule, style_popup, tall_popup, top_popup, ui_post

register(globals())                      # цвета темы лежат обычными именами модуля


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
    _actions_pause = False          # после листания кнопки «Отмена/+Шрифт/…» скрыты до новой отметки или выделения
    cur_sel = None                  # последнее выделение: {'key', 's', 'e'} — в координатах текста карточки
    _undo_n = 0

    def _on_undo_count(self, n):
        self._undo_n = n
        self._actions_pause = False                      # новая отметка/отмена — кнопки снова нужны
        self.refresh_actions()

    def page_changed(self):
        """Страница сменилась: выделение сброшено, ряд кнопок над страницей убран (вместо него снова статус)."""
        self.cur_sel = None
        self._actions_pause = True
        self.refresh_actions()

    def refresh_actions(self):
        """Кнопки «Отмена / +Шрифт / +Цвет / +Мысли» видны, пока есть что отменять или выделено слово."""
        on = (self._undo_n > 0 or self.cur_sel is not None) and not self.ctl.marks_only() and not self._actions_pause
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
        self._actions_pause = False
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
        self._actions_pause = False
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
