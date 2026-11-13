"""Диалоги: о программе, пароль, синхронизация, «Продолжить» чтение, «Дополнить», экспорт, дневник."""
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.core.window import Window
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.progressbar import ProgressBar
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from .. import __version__
from ..search import escape_markup
from .picker import publish_shared, share_file
from .widgets import ReadBox, ShareIcon, btn, mlabel, no_bubble, style_popup, tall_popup, top_popup
from .theme import register

register(globals())


class DialogsMixin:
    """Диалоги: о программе, пароль, синхронизация, «Продолжить» чтение, «Дополнить», экспорт, дневник. Методы смешиваются в SinevaApp."""

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
