"""Дымовой прогон интерфейса на заглушках Kivy (настоящего Kivy здесь нет): ловит опечатки и неверные имена."""
import os, sys, tempfile, types
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))


class W:
    def __init__(self, *a, **kw):
        self.__dict__.update(kw); self.children = []; self.manager = None; self.parent = None; self.refs = {}; self.texture_size = (0, 0); self.center_x = 0; self.center_y = 0; self.do_scroll_y = True; self.text = kw.get('text', ''); self.height = 100; self.scroll_y = 1
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
mod('kivy.core'); mod('kivy.core.window', Window=W())
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
app.settings_s.export('txt')
app.open_append()
app.open_diary()
app.settings_s.share_diary()
# выделение: долгое нажатие на слово и подчёркивание
from sineva.ui.app import SelectableText
cd = c.outcome and [x for x in c._publish.__self__.search.visible(c.outcome)]
cards = []
c.on_cards = lambda cs, t, m: cards.extend(cs)
c._publish()
w = SelectableText(cards[0], app.on_selection, c.on_status)
w.toggle_select_mode()
ids = [i for i, r in w._refs_info.items() if cards[0]['text'][r['start']:r['end']].strip()]
w.on_ref_press(str(ids[0]))
first = w.sel
w.on_ref_press(str(ids[min(2, len(ids) - 1)]))
print('выделено касаниями:', first, '->', w.sel)
app.books_s.on_pre_enter(); app.on_resume(); app.settings_s.add_shortcut()
print('UI smoke OK, карточек:', len(app.search_s.box.children), 'книг:', len(app.books_s.box.children),
      'подчёркнутых:', len(c.marks.all_marks()))
