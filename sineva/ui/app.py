"""Интерфейс «Синева+ Mobile» на Kivy. Вся логика — в sineva.controller.Controller; здесь только виджеты."""
from kivy.app import App
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle
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
from kivy.uix.widget import Widget
from kivy.uix.textinput import TextInput

from .. import __version__
from ..controller import Controller
from ..search import escape_markup
from .picker import add_home_shortcut, pick_files, share_file
from .textmodel import atom_at, build_markup, sentence_bounds

BG = (0.97, 0.97, 0.98, 1)
CARD = (1, 1, 1, 1)
TXT = (0.1, 0.1, 0.12, 1)
ACCENT = (0.04, 0.34, 0.82, 1)
GRAY = (0.45, 0.45, 0.5, 1)
YELLOW = (1, 1, 0, 1)                    # слово запроса: #FFFF00 (как на ПК)
YELLOW_SOFT = (1, 1, 0.6, 1)             # часть слова: #FFFF99
SEL_BLUE = (0.67, 0.8, 1, 1)             # выделение пальцем: #AACCFF

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


def vlist(on_zoom=None):
    sv = ZoomScroll(on_zoom, bar_width=dp(4)) if on_zoom else ScrollView(bar_width=dp(4))
    box = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(6), padding=dp(6))
    box.bind(minimum_height=box.setter('height'))
    sv.add_widget(box)
    return sv, box


class SelectableText(Label):
    """Текст топика: жёлтый маркер на найденных словах, подчёркнутое и выделение.
       Касание слова (без сдвига пальца) выделяет это слово, касание второго слова — весь фрагмент между ними;
       тогда вызывается on_select(card_key, start, end, виджет). Прокрутка и щипок работают как обычно."""

    TAP_MAX_MOVE = 12            # dp: больше — это уже прокрутка, а не касание

    def __init__(self, card, on_select, on_hint=lambda t: None, font_pt=16, on_activate=lambda w: None, **kw):
        kw.setdefault('color', (0.05, 0.05, 0.05, 1))
        super().__init__(markup=True, size_hint_y=None, halign='left', valign='top', font_size=sp(font_pt), **kw)
        self.card, self.on_select, self.on_hint, self.on_activate = card, on_select, on_hint, on_activate
        self.sel = None
        self.select_mode = False
        self._refs_info = {}
        self._anchor = None
        self.bind(width=lambda w, v: setattr(w, 'text_size', (v, None)))
        self.bind(texture_size=self._on_texture)
        self.bind(pos=lambda *_: self._redraw(), size=lambda *_: self._redraw())
        self._refresh()

    # --- разметка и подложки
    def _refresh(self):
        c = self.card
        self.text, self._refs_info = build_markup(c['text'], c['hl'], c['marks'], self.sel, self.select_mode)

    def _apply(self):
        """Один пересчёт текстуры после смены разметки."""
        self._refresh()
        self.texture_update()
        self._redraw()

    def _on_texture(self, w, ts):
        self.height = ts[1] + dp(10)
        self._redraw()

    def _redraw(self):
        self.canvas.before.clear()
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
                    Rectangle(pos=(ox + x1, oy - y2), size=(x2 - x1, y2 - y1))

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

    def _tap(self, pos):
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
            return
        sel = (min(self._anchor[0], word[0]), max(self._anchor[1], word[1]))
        self._anchor = None
        self.sel = sel
        self._apply()
        self.on_select(self.card['key'], sel[0], sel[1], self)

    def clear_selection(self):
        self.sel = None
        self._anchor = None
        if self.select_mode:
            self.select_mode = False
            self._apply()


class ZoomScroll(ScrollView):
    """Прокрутка + щипок двумя пальцами: разведение увеличивает шрифт, сведение уменьшает."""

    def __init__(self, on_zoom, **kw):
        super().__init__(**kw)
        self.on_zoom = on_zoom
        self._touches = []
        self._pinch = False
        self._d0 = 0

    def _dist(self):
        (a, b) = self._touches[:2]
        return ((a.pos[0] - b.pos[0]) ** 2 + (a.pos[1] - b.pos[1]) ** 2) ** 0.5

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            self._touches.append(touch)
            if len(self._touches) == 2:
                self._pinch = True
                self._d0 = max(self._dist(), 1)
                self._ratio = 1.0
                for t in self._touches:
                    t.ud['pinch'] = True
                self._prev_scroll = self.do_scroll_y
                self.do_scroll_y = False
                return True
        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        if self._pinch:
            if len(self._touches) >= 2:
                self._ratio = self._dist() / self._d0
            return True
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if touch in self._touches:
            self._touches.remove(touch)
            if self._pinch:
                if len(self._touches) < 2:
                    ratio = self._ratio
                    self._pinch = False
                    self.do_scroll_y = getattr(self, '_prev_scroll', True)
                    if abs(ratio - 1) > 0.08:
                        self.on_zoom(ratio)
                return True
        return super().on_touch_up(touch)


# ===================================================================
class SearchScreen(Screen):
    def __init__(self, app, **kw):
        super().__init__(name='search', **kw)
        self.app, self.ctl = app, app.ctl
        root = BoxLayout(orientation='vertical', spacing=dp(4), padding=dp(6))
        top = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(4))
        self.input = TextInput(hint_text='Слово или часть слова (вер.а — точка ограничивает)', multiline=False,
                               font_size=sp(17), write_tab=False)
        self.input.bind(text=lambda w, v: self.ctl.on_query_changed(v))      # только запоминает запрос
        self.input.bind(on_text_validate=lambda *_: self.search())
        self.go = Button(text='Поиск', size_hint_x=None, width=dp(88), background_normal='',
                         background_color=ACCENT, color=(1, 1, 1, 1))
        self.go.bind(on_release=lambda *_: self.search())
        top.add_widget(self.input)
        top.add_widget(self.go)
        self.status = mlabel('Введите слово и нажмите «Поиск». Книги выбираются на вкладке «Книги».', font_size=sp(13))
        self.status.color = GRAY
        self.sv, self.box = vlist(lambda r: self.app.zoom(r))
        self._active = None
        root.add_widget(top)
        root.add_widget(self.status)
        root.add_widget(self.sv)
        self.add_widget(root)

    def search(self):
        self.input.focus = False
        self.ctl.submit_query(self.input.text)

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

    def _activate(self, widget):
        if self._active is not None and self._active is not widget:
            self._active.clear_selection()
        self._active = widget

    def _card(self, c):
        box = BoxLayout(orientation='vertical', size_hint_y=None, padding=dp(8), spacing=dp(2))
        box.bind(minimum_height=box.setter('height'))
        head = mlabel(c['header'], font_size=sp(14))
        body = SelectableText(c, self.app.on_selection, self.ctl.on_status,
                              self.ctl.config.get_int('font_size', 16), self._activate)
        key = c['key']
        box.add_widget(head)
        box.add_widget(body)
        if self.ctl.config.get_bool('brief_view', False):         # «Полностью» нужна только в кратком виде
            more = Button(text='Свернуть' if c['expanded'] else 'Полностью', size_hint_y=None, height=dp(34),
                          background_normal='', background_color=(0.9, 0.92, 0.97, 1), color=ACCENT)
            more.bind(on_release=lambda *_: self.ctl.toggle_expand(key))
            box.add_widget(more)
        from kivy.graphics import RoundedRectangle
        with box.canvas.before:
            Color(*CARD)
            rect = RoundedRectangle(radius=[dp(8)])
        box.bind(pos=lambda w, v: setattr(rect, 'pos', v), size=lambda w, v: setattr(rect, 'size', v))
        return box


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
        self.status = mlabel('', font_size=sp(13))
        self.status.color = ACCENT
        root.add_widget(self.status)
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
        pick_files(lambda p: Clock.schedule_once(lambda dt: got(p), 0),
                   self.ctl.config.get('picker_start', ''),
                   lambda uri: self.ctl.set_setting('picker_start', uri),
                   self.ctl.paths.books_dir,
                   lambda t: Clock.schedule_once(lambda dt: self.ctl.on_status(t), 0))

    def on_pre_enter(self, *a):
        self.ctl.rescan()                    # вручную скопированные файлы видны сразу

    def index_needed(self):
        self.ctl.index_books([r['file_name'] for r in self._rows if r['state'] != 'ready'])

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
        self.app, self.ctl = app, app.ctl
        root = BoxLayout(orientation='vertical', spacing=dp(8), padding=dp(10))
        brief = BoxLayout(size_hint_y=None, height=dp(44))
        brief.add_widget(mlabel('Краткий вид (только предложения с совпадениями)'))
        sw = Switch(active=self.ctl.config.get_bool('brief_view', False), size_hint_x=None, width=dp(90))
        sw.bind(active=lambda w, v: (self.ctl.set_setting('brief_view', 1 if v else 0), self.ctl._publish()))
        brief.add_widget(sw)
        root.add_widget(brief)
        root.add_widget(btn('В дневник…', self.app.open_diary))
        root.add_widget(btn('Дополнить книгу…', self.app.open_append))
        root.add_widget(Widget(size_hint_y=None, height=dp(24)))         # пустая строка перед кнопками «Поделиться»
        root.add_widget(btn('Подчёркнутое → TXT и поделиться', lambda: self.export('txt'), color=GRAY))
        root.add_widget(btn('Подчёркнутое → Word (DOCX) и поделиться', lambda: self.export('docx'), color=GRAY))
        root.add_widget(btn('Поделиться конспектом', self.share_conspect, color=GRAY))
        root.add_widget(btn('Поделиться дневником', self.share_diary, color=GRAY))
        root.add_widget(Widget(size_hint_y=None, height=dp(24)))
        root.add_widget(btn('Ярлык на главный экран', self.add_shortcut, color=GRAY))
        self.info = mlabel('', font_size=sp(13))
        root.add_widget(self.info)
        root.add_widget(BoxLayout())
        self.add_widget(root)

    def export(self, fmt):
        path = self.ctl.export_marks(fmt)
        if path:
            share_file(path, self.ctl.on_status)

    def add_shortcut(self):
        add_home_shortcut(self.ctl.on_status)

    def share_diary(self):
        path = self.ctl.diary_file()
        if path:
            share_file(path, self.ctl.on_status)

    def share_conspect(self):
        path = self.ctl.conspect_file()
        if path:
            share_file(path, self.ctl.on_status)

    def on_pre_enter(self, *a):
        v = self.ctl.vector_status()
        vec = ('Смысловой поиск: модели подключены — ' + ', '.join(v['models'])) if v['ready'] else \
              'Смысловой поиск (векторные модели): пока не подключён, архитектура готова.'
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
        self.books_s = BooksScreen(self)
        self.settings_s = SettingsScreen(self)
        for s in (self.search_s, self.books_s, self.settings_s):
            self.sm.add_widget(s)
        c = self.ctl
        c.on_cards = self.search_s.show_cards
        c.on_status = lambda t: (self.search_s.set_status(t), self.books_s.set_status(t))
        c.on_books = self.books_s.show_books
        c.on_progress = self.books_s.show_progress

        root = BoxLayout(orientation='vertical')
        root.add_widget(self.sm)
        nav = BoxLayout(size_hint_y=None, height=dp(52))
        for label, name in (('Поиск', 'search'), ('Книги', 'books'), ('Ещё', 'settings')):
            b = Button(text=label, background_normal='', background_color=(0.92, 0.93, 0.96, 1), color=ACCENT)
            b.bind(on_release=lambda *_a, n=name: setattr(self.sm, 'current', n))
            nav.add_widget(b)
        root.add_widget(nav)
        Window.bind(on_keyboard=self._on_key)
        c.publish_books()
        if not c.library.books:
            self.sm.current = 'books'
        return root

    def on_resume(self):
        self.ctl.rescan()
        return True

    # ---------- выделенный фрагмент: автоподчёркивание + меню ----------
    def on_selection(self, key, start, end, widget):
        """Окно подтверждения. Окно показывается сразу; подчёркивание и запись в конспект — по кнопке."""
        text = widget.card['text']
        ctl = self.ctl
        chosen = text[start:end].strip()
        box = BoxLayout(orientation='vertical', spacing=dp(6), padding=dp(8))
        box.add_widget(mlabel(escape_markup(chosen if len(chosen) < 300 else chosen[:300] + '…'), font_size=sp(14)))
        pop = Popup(title='Выделено', content=box, size_hint=(0.95, None), height=dp(400), auto_dismiss=False)

        def finish(mark=None, unmark=False):
            pop.dismiss()
            def work(dt):
                if mark:
                    ctl.mark_selection(key, start, end, publish=False)
                if unmark:
                    ctl.unmark_selection(key, start, end, publish=False)
                widget.card['marks'] = ctl.card_marks(key)      # обновляется только эта карточка, не весь список
                widget.clear_selection()
            Clock.schedule_once(work, 0)

        def to_conspect():
            ctl.quote_to_conspect(key, chosen)                  # цитата + источник в конспект
            finish(mark=True)                                   # и тут же подчёркивание

        def only_mark():
            finish(mark=True)

        def copy():
            Clipboard.copy(chosen)
            ctl.on_status('Скопировано')
            finish()

        def sentence():
            a, b = sentence_bounds(text, start, end)
            pop.dismiss()
            widget.sel = (a, b)
            widget._apply()
            self.on_selection(key, a, b, widget)

        def cancel():
            finish()

        def unmark():
            finish(unmark=True)

        rows = [('В конспект и подчеркнуть', to_conspect, ACCENT), ('Только подчеркнуть', only_mark, GRAY),
                ('Копировать', copy, GRAY), ('Всё предложение', sentence, GRAY)]
        if ctl.selection_marked(key, start, end):
            rows.append(('Снять подчёркивание', unmark, (0.7, 0.15, 0.15, 1)))
        rows.append(('Отмена', cancel, GRAY))
        for label, fn, color in rows:
            box.add_widget(btn(label, fn, color=color))
        pop.height = dp(120 + 50 * len(rows))
        pop.open()

    # ---------- размер шрифта (щипок) ----------
    def zoom(self, ratio):
        size = self.ctl.config.get_int('font_size', 16)
        new = max(10, min(40, round(size * ratio)))
        if new != size:
            self.ctl.set_setting('font_size', new)
            self.ctl._publish()
            self.ctl.on_status(f'Шрифт: {new}')

    # ---------- «Дополнить» ----------
    def open_append(self):
        ctl = self.ctl
        books = ctl.library.books
        if not books:
            ctl.on_status('Сначала добавьте книги')
            return
        names = {f"{b.display} · {b.file_name}": b.file_name for b in books}
        remembered = ctl.append_target()
        first = next((k for k, v in names.items() if v == remembered), next(iter(names)))
        box = BoxLayout(orientation='vertical', spacing=dp(6), padding=dp(8))
        spin = Spinner(text=first, values=list(names), size_hint_y=None, height=dp(44))
        text = TextInput(hint_text='Вставьте текст — он добавится в конец выбранной книги', multiline=True)
        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        pop = Popup(title='Дополнить книгу (выбор запоминается)', content=box, size_hint=(0.97, 0.9))

        def paste():
            text.text = text.text + (Clipboard.paste() or '')

        def add():
            if not text.text.strip():
                return
            if self.ctl.append_to_book(names[spin.text], text.text):
                pop.dismiss()

        row.add_widget(btn('Вставить из буфера', paste, color=GRAY))
        row.add_widget(btn('Добавить', add))
        row.add_widget(btn('Отмена', pop.dismiss, color=GRAY))
        for w in (spin, text, row):
            box.add_widget(w)
        pop.open()

    def open_diary(self):
        box = BoxLayout(orientation='vertical', spacing=dp(6), padding=dp(8))
        text = TextInput(hint_text='Запись добавится в конец файла dnev.txt (он есть в списке книг)', multiline=True)
        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        pop = Popup(title='В дневник', content=box, size_hint=(0.97, 0.9))

        def paste():
            text.text = text.text + (Clipboard.paste() or '')

        def add():
            if self.ctl.add_to_diary(text.text):
                pop.dismiss()

        row.add_widget(btn('Вставить из буфера', paste, color=GRAY))
        row.add_widget(btn('Записать', add))
        row.add_widget(btn('Отмена', pop.dismiss, color=GRAY))
        box.add_widget(text)
        box.add_widget(row)
        pop.open()

    def _on_key(self, window, key, *a):
        if key == 27 and self.sm.current != 'search':       # «Назад» возвращает на поиск
            self.sm.current = 'search'
            return True
        return False
