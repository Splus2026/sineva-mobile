"""Дымовой прогон интерфейса на заглушках Kivy (настоящего Kivy здесь нет): ловит опечатки и неверные имена."""
import os, sys, tempfile, types
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))


class W:
    def __init__(self, *a, **kw):
        self.__dict__.update(kw); self.children = []; self.text = kw.get('text', ''); self.height = 100; self.scroll_y = 1
        self.canvas = types.SimpleNamespace(before=_Ctx())
    def bind(self, **kw): pass
    def add_widget(self, w): self.children.append(w)
    def clear_widgets(self): self.children = []
    def setter(self, n): return lambda *a: None
    def open(self): pass
    def dismiss(self): pass
    def __getattr__(self, n): return lambda *a, **k: None
class _Ctx:
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
mod('kivy.uix'); mod('kivy.uix.screenmanager', NoTransition=W, Screen=W, ScreenManager=W)
mod('kivy.uix.filechooser', FileChooserListView=W)
mod('kivy.graphics', Color=W, RoundedRectangle=W)

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
c.on_query_changed('вера'); c.submit_query()
c.toggle_word(c.word_rows()[0]['word']); c.exclude_all_words(); c.show_more()
app.books_s.edit(c.book_rows()[0]); app.settings_s.on_pre_enter()
app.search_s.toggle_mode(); app.books_s.add()
print('UI smoke OK, карточек:', len(app.search_s.box.children), 'книг:', len(app.books_s.box.children))
