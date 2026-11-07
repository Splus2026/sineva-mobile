"""Интерфейс «Синева+ Mobile» на Kivy. Вся логика — в sineva.controller.Controller; здесь только виджеты."""
from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.progressbar import ProgressBar
from kivy.uix.screenmanager import NoTransition, Screen, ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.switch import Switch
from kivy.uix.textinput import TextInput

from .. import __version__
from ..controller import Controller
from ..search import escape_markup
from .picker import pick_files

BG = (0.97, 0.97, 0.98, 1)
CARD = (1, 1, 1, 1)
TXT = (0.1, 0.1, 0.12, 1)
ACCENT = (0.04, 0.34, 0.82, 1)
GRAY = (0.45, 0.45, 0.5, 1)

SEPARATORS = ['N1000', 'N500', 'N2000', 'NS300', 'NS800', 'Numeric', 'ExcelRow1']


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


def keep_scroll(sv: ScrollView, rebuild):
    """Перестраивает содержимое, сохраняя положение прокрутки в пикселях (список не прыгает в начало)."""
    content = sv.children[0] if sv.children else None
    if content is None:
        rebuild()
        return
    off = (1 - sv.scroll_y) * max(0, content.height - sv.height)
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


# ===================================================================
class SearchScreen(Screen):
    def __init__(self, app, **kw):
        super().__init__(name='search', **kw)
        self.app, self.ctl = app, app.ctl
        root = BoxLayout(orientation='vertical', spacing=dp(4), padding=dp(6))
        top = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(4))
        self.input = TextInput(hint_text='Слово или часть слова (вер.а — точка ограничивает)', multiline=False,
                               font_size=sp(17), write_tab=False)
        self.input.bind(text=lambda w, v: self.ctl.on_query_changed(v))
        self.input.bind(on_text_validate=lambda *_: self.ctl.submit_query())
        self.mode_btn = Button(text='Слова', size_hint_x=None, width=dp(84), background_normal='',
                               background_color=GRAY, color=(1, 1, 1, 1))
        self.mode_btn.bind(on_release=lambda *_: self.toggle_mode())
        top.add_widget(self.input)
        top.add_widget(self.mode_btn)
        self.status = mlabel('Выберите книги на вкладке «Книги»', font_size=sp(13))
        self.status.color = GRAY
        self.sv, self.box = vlist()
        root.add_widget(top)
        root.add_widget(self.status)
        root.add_widget(self.sv)
        self.add_widget(root)

    def toggle_mode(self):
        new = 'vector' if self.ctl.mode == 'text' else 'text'
        self.mode_btn.text = 'Смысл' if new == 'vector' else 'Слова'
        self.ctl.set_mode(new)

    def set_status(self, text):
        self.status.text = escape_markup(text)

    def show_cards(self, cards, total, more):
        def rebuild():
            self.box.clear_widgets()
            for c in cards:
                self.box.add_widget(self._card(c))
            if more:
                self.box.add_widget(btn(f'Показать ещё (всего {total})', self.ctl.show_more))
            elif not cards and self.ctl.query.strip():
                self.box.add_widget(mlabel('Ничего не найдено', color=GRAY))
        keep_scroll(self.sv, rebuild)

    def _card(self, c):
        box = BoxLayout(orientation='vertical', size_hint_y=None, padding=dp(8), spacing=dp(2))
        box.bind(minimum_height=box.setter('height'))
        head = mlabel(c['header'], font_size=sp(14))
        body = mlabel(c['body'])
        key = c['key']
        more = Button(text='Свернуть' if c['expanded'] else 'Полностью', size_hint_y=None, height=dp(34),
                      background_normal='', background_color=(0.9, 0.92, 0.97, 1), color=ACCENT)
        more.bind(on_release=lambda *_: self.ctl.toggle_expand(key))
        for w in (head, body, more):
            box.add_widget(w)
        from kivy.graphics import Color, RoundedRectangle
        with box.canvas.before:
            Color(*CARD)
            rect = RoundedRectangle(radius=[dp(8)])
        box.bind(pos=lambda w, v: setattr(rect, 'pos', v), size=lambda w, v: setattr(rect, 'size', v))
        return box


class WordsScreen(Screen):
    def __init__(self, app, **kw):
        super().__init__(name='words', **kw)
        self.ctl = app.ctl
        root = BoxLayout(orientation='vertical', spacing=dp(4), padding=dp(6))
        root.add_widget(mlabel('Совпадения: нажмите слово, чтобы вычеркнуть его из результатов.', font_size=sp(13)))
        root.add_widget(btn('Вычеркнуть все / вернуть все', self.ctl.exclude_all_words, color=GRAY))
        self.sv, self.box = vlist()
        root.add_widget(self.sv)
        self.add_widget(root)
        self._labels = {}

    def show_words(self, rows):
        """Если набор слов не изменился, обновляем подписи на месте: список остаётся там, где был."""
        order = [r['word'] for r in rows]
        if order == list(self._labels):
            for r in rows:
                self._labels[r['word']].text = self.ctl.search.word_markup(r)
            return

        def rebuild():
            self.box.clear_widgets()
            self._labels = {}
            for r in rows:
                b = Button(markup=True, text=self.ctl.search.word_markup(r), size_hint_y=None, height=dp(40),
                           background_normal='', background_color=CARD, halign='left')
                b.bind(size=lambda w, v: setattr(w, 'text_size', (v[0] - dp(12), None)))
                w = r['word']
                b.bind(on_release=lambda *_a, w=w: self.ctl.toggle_word(w))
                self._labels[w] = b
                self.box.add_widget(b)
        keep_scroll(self.sv, rebuild)


class BooksScreen(Screen):
    def __init__(self, app, **kw):
        super().__init__(name='books', **kw)
        self.app, self.ctl = app, app.ctl
        root = BoxLayout(orientation='vertical', spacing=dp(4), padding=dp(6))
        bar = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(4))
        bar.add_widget(btn('Добавить', self.add))
        bar.add_widget(btn('↻', self.ctl.refresh, color=GRAY, size_hint_x=None, width=dp(60)))
        bar.add_widget(btn('Индексировать', self.index_needed, color=GRAY))
        root.add_widget(bar)
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
                self.ctl.index_books(added)
        pick_files(lambda p: Clock.schedule_once(lambda dt: got(p), 0))

    def index_needed(self):
        self.ctl.index_books([r['file_name'] for r in self._rows if r['state'] != 'ready'])

    def show_progress(self, name, done, total):
        self.progress.value = 100 * done / total if total else 0

    def show_books(self, rows):
        self._rows = rows

        def rebuild():
            self.box.clear_widgets()
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
                      f"{escape_markup(r['status'])} · {r['separator']}[/color][/size]")
        info.size_hint_y = 1
        info.bind(height=lambda *_: None)
        edit = Button(text='⚙', size_hint_x=None, width=dp(48), background_normal='', background_color=GRAY)
        edit.bind(on_release=lambda *_a, r=r: self.edit(r))
        for w in (cb, info, edit):
            row.add_widget(w)
        return row

    def edit(self, r):
        box = BoxLayout(orientation='vertical', spacing=dp(6), padding=dp(8))
        name = TextInput(text=r['display'], multiline=False, size_hint_y=None, height=dp(44))
        sep = Spinner(text=r['separator'], values=SEPARATORS, size_hint_y=None, height=dp(44))
        sep_custom = TextInput(hint_text='или свой разделитель (N700, NS400, строка…)', multiline=False,
                               size_hint_y=None, height=dp(44))
        for w in (mlabel('Название'), name, mlabel('Разбивка на топики'), sep, sep_custom):
            box.add_widget(w)
        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        pop = Popup(title=r['file_name'], content=box, size_hint=(0.95, None), height=dp(420))

        def save():
            pop.dismiss()
            self.ctl.edit_book(r['file_name'], name.text.strip() or r['display'],
                               sep_custom.text.strip() or sep.text)
            self.ctl.index_books([r['file_name']])

        def remove():
            pop.dismiss()
            self.ctl.remove_book(r['file_name'], delete_file=False)
        row.add_widget(btn('Сохранить', save))
        row.add_widget(btn('Убрать', remove, color=(0.7, 0.15, 0.15, 1)))
        box.add_widget(row)
        pop.open()


class SettingsScreen(Screen):
    def __init__(self, app, **kw):
        super().__init__(name='settings', **kw)
        self.ctl = app.ctl
        root = BoxLayout(orientation='vertical', spacing=dp(8), padding=dp(10))
        brief = BoxLayout(size_hint_y=None, height=dp(44))
        brief.add_widget(mlabel('Краткий вид (только предложения с совпадениями)'))
        sw = Switch(active=self.ctl.config.get_bool('brief_view', True), size_hint_x=None, width=dp(90))
        sw.bind(active=lambda w, v: (self.ctl.set_setting('brief_view', 1 if v else 0), self.ctl._publish()))
        brief.add_widget(sw)
        root.add_widget(brief)
        self.info = mlabel('', font_size=sp(13))
        root.add_widget(self.info)
        root.add_widget(BoxLayout())
        self.add_widget(root)

    def on_pre_enter(self, *a):
        v = self.ctl.vector_status()
        vec = ('Смысловой поиск: модели подключены — ' + ', '.join(v['models'])) if v['ready'] else \
              'Смысловой поиск (векторные модели): пока не подключён. Архитектура готова, модели появятся позже.'
        if v['problems']:
            vec += '\n' + '\n'.join(escape_markup(p) for p in v['problems'])
        self.info.text = (f"Синева+ Mobile {__version__}\nПапка книг:\n{escape_markup(self.ctl.paths.books_dir)}\n"
                          f"Конфигурация:\n{escape_markup(self.ctl.paths.config_path)}\n\n{vec}")


# ===================================================================
class SinevaApp(App):
    title = 'Синева+'

    def build(self):
        Window.clearcolor = BG
        self.ctl = Controller(ui_post=ui_post, schedule=schedule)
        self.sm = ScreenManager(transition=NoTransition())
        self.search_s = SearchScreen(self)
        self.words_s = WordsScreen(self)
        self.books_s = BooksScreen(self)
        self.settings_s = SettingsScreen(self)
        for s in (self.search_s, self.words_s, self.books_s, self.settings_s):
            self.sm.add_widget(s)
        c = self.ctl
        c.on_cards = self.search_s.show_cards
        c.on_status = self.search_s.set_status
        c.on_words = self.words_s.show_words
        c.on_books = self.books_s.show_books
        c.on_progress = self.books_s.show_progress

        root = BoxLayout(orientation='vertical')
        root.add_widget(self.sm)
        nav = BoxLayout(size_hint_y=None, height=dp(52))
        for label, name in (('Поиск', 'search'), ('Слова', 'words'), ('Книги', 'books'), ('Ещё', 'settings')):
            b = Button(text=label, background_normal='', background_color=(0.92, 0.93, 0.96, 1), color=ACCENT)
            b.bind(on_release=lambda *_a, n=name: setattr(self.sm, 'current', n))
            nav.add_widget(b)
        root.add_widget(nav)
        Window.bind(on_keyboard=self._on_key)
        c.publish_books()
        if not c.library.books:
            self.sm.current = 'books'
        return root

    def _on_key(self, window, key, *a):
        if key == 27 and self.sm.current != 'search':       # «Назад» возвращает на поиск
            self.sm.current = 'search'
            return True
        return False
