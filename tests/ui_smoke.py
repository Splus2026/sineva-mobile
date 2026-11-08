"""Дымовой прогон интерфейса на заглушках Kivy (настоящего Kivy здесь нет): ловит опечатки и неверные имена."""
import os, sys, tempfile, types
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))


class W:
    def __init__(self, *a, **kw):
        self.__dict__.update(kw); self.children = []; self.manager = None; self.parent = None; self.refs = {}; self.texture_size = (0, 0); self.center_x = 0; self.center_y = 0; self.x = 0; self.y = 0; self.do_scroll_y = True; self.text = kw.get('text', ''); self.height = 100; self.scroll_y = 1
        self.canvas = types.SimpleNamespace(before=_Ctx())
    def bind(self, **kw): pass
    def collide_point(self, x, y): return True
    def texture_update(self): pass
    def add_widget(self, w): self.children.append(w)
    def clear_widgets(self): self.children = []
    def setter(self, n): return lambda *a: None
    def open(self): pass
    def dismiss(self): pass
    def __getattr__(self, n): return lambda *a, **k: None
class _Ctx:
    def clear(self): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False

def mod(name, **attrs):
    m = types.ModuleType(name); m.__dict__.update(attrs); sys.modules[name] = m; return m

class Clock:
    @staticmethod
    def schedule_once(fn, d=0):
        fn(0); return types.SimpleNamespace(cancel=lambda: None)
class App(W):
    def run(self): pass
mod('kivy'); mod('kivy.app', App=App); mod('kivy.clock', Clock=Clock)
mod('kivy.core'); mod('kivy.core.window', Window=types.SimpleNamespace(height=800, width=400, bind=lambda **k: None, clearcolor=None, remove_widget=lambda w: None, add_widget=lambda w: None))
mod('kivy.metrics', dp=lambda x: x if isinstance(x, (int, float)) else 10, sp=lambda x: x)
for n, c in [('boxlayout', 'BoxLayout'), ('button', 'Button'), ('checkbox', 'CheckBox'), ('label', 'Label'),
             ('popup', 'Popup'), ('progressbar', 'ProgressBar'), ('scrollview', 'ScrollView'),
             ('spinner', 'Spinner'), ('switch', 'Switch'), ('textinput', 'TextInput')]:
    mod('kivy.uix.' + n, **{c: W})
mod('kivy.uix'); mod('kivy.uix.widget', Widget=W); mod('kivy.uix.screenmanager', NoTransition=W, Screen=W, ScreenManager=W)
mod('kivy.uix.filechooser', FileChooserListView=W)
mod('kivy.graphics', Color=W, RoundedRectangle=W, Rectangle=W)
clip = {}
mod('kivy.core.clipboard', Clipboard=types.SimpleNamespace(copy=lambda t: clip.update(t=t), paste=lambda: 'вставка'))

tmp = tempfile.mkdtemp()
import sineva.paths as sp
_orig = sp.AppPaths
sp.AppPaths = lambda *a, **k: _orig(root=tmp)
import sineva.controller as cc
cc.AppPaths = sp.AppPaths
open(os.path.join(tmp, 'books', 'a.txt'), 'w', encoding='utf-8') if os.path.isdir(os.path.join(tmp, 'books')) else None
os.makedirs(os.path.join(tmp, 'books'), exist_ok=True)
open(os.path.join(tmp, 'books', 'a.txt'), 'w', encoding='utf-8').write("1 Вера и доверие.\n2 Любовь.\n")
from sineva.ui.app import SinevaApp
app = SinevaApp()
app.build()
c = app.ctl
c.run_async = False
c.index_books(['a.txt'])
app.search_s.input.text = 'вера'
app.search_s.search()
card = app.search_s.box.children[0]
c.show_more()
app.books_s.edit(c.book_rows()[0]); app.settings_s.on_pre_enter(); app.books_s.add()
app.open_export()
app.settings_s.toggle_settings(); app.settings_s.toggle_settings()
app.root = object(); app.set_theme('night'); assert c.config.get('theme') == 'night'
from sineva.ui import textmodel as _tm; assert _tm.HL_TEXT == '000000'
app.set_theme('day'); assert _tm.HL_TEXT == ''
assert c.export_marks('txt') is None or c.list_exports()
app.open_append()
app.open_diary()
assert c.export_conspect() is None or c.list_exports()
# выделение: долгое нажатие на слово и подчёркивание
from sineva.ui.app import SelectableText
cd = c.outcome and [x for x in c._publish.__self__.search.visible(c.outcome)]
cards = []
c.on_cards = lambda cs, t, m: cards.extend(cs)
c._publish()
w = SelectableText(cards[0], app.on_selection, c.on_status)
w.texture_size = (400, 20); w.center_x = 200; w.center_y = 10
def zones():
    w.refs = {str(i): [(10 * i, 0, 10 * i + 9, 20)] for i in w._refs_info}
orig_apply = w._apply
def _apply():
    orig_apply(); zones()
w._apply = _apply
w._tap((12, 5))                       # первое касание: слово
first = w.sel
w._tap((32, 5))                       # второе касание: конец фрагмента -> сразу подчёркивание
print('выделено касаниями:', first, '->', w.sel)
app.search_s._activate(w)
app.change_font(+1); app.change_font(-8); app.settings_s.toggle_share(); app.settings_s.toggle_share(); app.settings_s.font(1)
print('шрифт:', c.config.get_int('font_size', 0))
print('отмечено:', c.selection_marked(cards[0]['key'], 0, 4), c.card_marks(cards[0]['key']))
app.books_s.on_pre_enter(); app.on_resume(); app.settings_s.add_shortcut()
print('UI smoke OK, карточек:', len(app.search_s.box.children), 'книг:', len(app.books_s.box.children),
      'подчёркнутых:', len(c.marks.all_marks()))
