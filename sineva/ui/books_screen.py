"""Экран «Книги»."""
from kivy.clock import Clock
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.checkbox import CheckBox
from kivy.uix.progressbar import ProgressBar
from kivy.uix.screenmanager import Screen
from kivy.uix.textinput import TextInput
from ..search import escape_markup
from .picker import pick_files
from .theme import register
from .widgets import PencilIcon, RefreshIcon, btn, icon_button, keep_scroll, mlabel, no_bubble, tall_popup, top_popup, vlist

register(globals())                      # цвета темы лежат обычными именами модуля


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
        self.sv.scroll_distance = dp(14)                 # прокрутка начинается позже, чем распознаётся направление жеста
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
