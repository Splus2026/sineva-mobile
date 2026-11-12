"""Дымовой прогон интерфейса на заглушках Kivy (настоящего Kivy здесь нет): ловит опечатки и неверные имена."""
import os, sys, tempfile, types
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))


class W:
    def __init__(self, *a, **kw):
        self.__dict__.update(kw); self.__dict__.setdefault('size', (100, 100)); self.__dict__.setdefault('pos', (0, 0)); self.children = []; self.manager = None; self.parent = None; self.refs = {}; self.texture_size = (0, 0); self.center_x = 0; self.center_y = 0; self.x = 0; self.y = 0; self.do_scroll_y = True; self.text = kw.get('text', ''); self.height = 100; self.scroll_y = 1; self.right = 100; self.width = kw.get('width', 100)
        self.canvas = _Ctx(); self.canvas.before = _Ctx(); self.canvas.after = _Ctx()
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
    @staticmethod
    def schedule_interval(fn, d=0):
        return types.SimpleNamespace(cancel=lambda: None)
class _Clock2: pass
class App(W):
    def run(self): pass
class Animation:
    def __init__(self, **kw): self.cb = None
    @staticmethod
    def cancel_all(w): pass
    def bind(self, on_complete=None): self.cb = on_complete
    def start(self, w):
        if self.cb: self.cb(self, w)
mod('kivy'); mod('kivy.animation', Animation=Animation); mod('kivy.app', App=App); mod('kivy.clock', Clock=Clock)
mod('kivy.core'); mod('kivy.core.window', Window=types.SimpleNamespace(height=800, width=400, bind=lambda **k: None, clearcolor=None, remove_widget=lambda w: None, add_widget=lambda w: None))
mod('kivy.metrics', dp=lambda x: x if isinstance(x, (int, float)) else 10, sp=lambda x: x)
for n, c in [('boxlayout', 'BoxLayout'), ('button', 'Button'), ('checkbox', 'CheckBox'), ('floatlayout', 'FloatLayout'), ('label', 'Label'),
             ('popup', 'Popup'), ('progressbar', 'ProgressBar'), ('scrollview', 'ScrollView'),
             ('spinner', 'Spinner'), ('switch', 'Switch'), ('textinput', 'TextInput')]:
    mod('kivy.uix.' + n, **{c: W})
mod('kivy.uix'); mod('kivy.uix.widget', Widget=W); mod('kivy.uix.screenmanager', NoTransition=W, Screen=W, ScreenManager=W); mod('kivy.uix.image', Image=W)
mod('kivy.uix.modalview', ModalView=type('ModalView', (), {})); mod('kivy.uix.filechooser', FileChooserListView=W)
mod('kivy.graphics', Color=W, RoundedRectangle=W, Rectangle=W, Ellipse=W, Line=W)
class _CV(_Ctx):
    pass

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
for _ in range(120): app.splash._tick(0.05)
assert app.splash.finished
c = app.ctl
c.run_async = False
c.index_books(['a.txt'])
app.search_s.input.text = 'вера'
app.search_s.search()
card = app.search_s.box.children[0]
c.show_more()
app.go_tab('read'); assert c.reading; c.show_more() if c.reading['has_next'] else None; app.go_tab('search'); assert c.reading is None
app.books_s.edit(c.book_rows()[0]); app.settings_s.on_pre_enter(); app.books_s.add()
app.open_export()
c.set_setting('index_auto', 1); c.rescan(); c.set_setting('index_auto', 0)
app.settings_s.toggle_settings(); app.settings_s.toggle_settings()
app.root = object(); app.set_theme('night'); assert c.config.get('theme') == 'night'
from sineva.ui import textmodel as _tm; assert _tm.HL_TEXT == '000000'
app.set_theme('day'); assert _tm.HL_TEXT == ''
assert c.export_marks('txt') is None or c.list_exports()
app.open_append()
app.open_diary()
assert c.export_conspect() is None or c.list_exports()
app.open_about(); app.confirm_exit(); app.open_password(); app.ask_password(); app._locked = False
app.search_s.set_undo(True); app.search_s.set_undo(False)
app.books_s.choose_to_index([{'file_name': 'a.txt', 'display': 'A', 'size_kb': 1}, {'file_name': 'b.txt', 'display': 'B', 'size_kb': 2}])
app.books_s.index_needed()
assert c.folder_sizes()['books'] >= 0
assert c.outcome.results == [] and c.reading is None      # смена вида/темы сбросила вкладки
c.submit_query('вера')
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

# ---- стандартная отрисовка: выделение слова, слои, листание с анимацией
app2 = SinevaApp()
app2.build()
for _ in range(120): app2.splash._tick(0.05)
c2 = app2.ctl
c2.run_async = False
app2.root = object(); app2.set_theme('night'); app2.set_theme('day')
app2.settings_s.toggle_settings(); app2.settings_s.toggle_settings()
c2.index_books(['a.txt'])
c2.submit_query('вера')
key = c2.outcome.results[0]['book'] + ':' + str(c2.outcome.results[0]['rowid'])
app2.sel_done(key, 0, 4, 'Вера')
assert len(c2.marks.all_marks()) >= 1
app2.undo_mark()
app2.search_s.clear()
app2.search_s.turn(1); app2.search_s.turn(-1)
if len(c2.search.visible(c2.outcome)) > 1:
    p0 = c2.nav['pos']; c2.go(1); assert c2.nav['pos'] == p0 + 1
    c2.go(-1); assert c2.nav['pos'] == p0
# перетаскивание страницы: начало, движение в обе стороны, отпускание (с переходом и без)
ss = app2.search_s
ss.page.width = 400
ss._drag_begin(); ss._drag_move(-150); ss._drag_move(80); ss._drag_end(-20, 0.5)
ss._anim_busy = False; ss._drag_begin(); ss._drag_move(-300); ss._drag_end(-300, 0.5); ss._drop_cover()
ss._anim_busy = False
assert c2.peek(-1) is None or c2.peek(-1)['key']
ss._fontbar_show(); ss._font_change(1); ss._font_change(-1); ss._fontbar_hide()
app2.settings_s.toggle_settings(); app2.settings_s.swap_floor(50); app2.settings_s.swap_floor(-5000); app2.settings_s.toggle_settings()
assert c2.swap_min_ms() == 0
app2.on_anchor(key, 0, 4, None)
assert app2.cur_sel and app2.cur_sel['key'] == key
app2.apply_style('big'); app2.apply_style('color')
app2.cur_sel = {'key': key, 's': 0, 'e': 4}
app2.open_note_editor()
nid = c2.add_note(key, 0, 4, 'моя мысль')
app2.on_note_tap(nid); app2.open_note_editor(nid)
c2.toggle_note_flag(nid, 'u'); c2.edit_note_text(nid, 'иначе')
before = len(c2.undo_stack)
app2.undo_mark(); assert len(c2.undo_stack) == before - 1
assert c2.export_notes()
for _ in range(10): app2.undo_mark()
assert not c2.undo_stack
# режим «Отметки» и архив папки синхронизации
c2.set_setting('marks_view', 1); c2.reset_views(); c2.submit_query('вера')
k2 = c2.outcome.results[0]['book'] + ':' + str(c2.outcome.results[0]['rowid'])
cd2 = c2.card_dict(k2); print('Отметки:', repr(cd2['text']))
assert cd2['text'] == '(отметок нет)' or '…' in cd2['text'] or cd2['marks']
assert c2.toggle_mark(k2, 0, 4, 'x') is None and c2.add_note(k2, 0, 4, 'z') is None
app2.sel_done(k2, 0, 4, 'x'); app2.apply_style('big'); app2.open_note_editor(); app2.undo_mark()
c2.set_setting('marks_view', 0); c2.reset_views()
import tempfile as _tf, zipfile as _zf
from sineva.syncmerge import DirFolder
d = _tf.mkdtemp(); open(d + '/dnev.txt', 'w').write('дневник'); open(d + '/sineva_marks.json', 'w').write('{}')
res = []
saved = []
c2.save_download = lambda p: (saved.append(_zf.ZipFile(p).namelist()), 'Download/x.zip')[1]
c2.backup_sync_folder(lambda ok, t: res.append((ok, t)), DirFolder(d))
assert res and res[0][0] and sorted(saved[0]) == ['dnev.txt', 'sineva_marks.json'], res
c2.backup_sync_folder(lambda ok, t: res.append((ok, t)), DirFolder(_tf.mkdtemp())); assert not res[1][0]
app2.sync_dialog('start', 'x'); app2.sync_dialog('done', 'ok'); app2.sync_dialog('start', 'a'); app2.sync_dialog('backup', 'b'); app2._sync_close()
app2.ask_continue('Книга', [(1, 1, 'Первое.'), (2, 2, 'Второе.')], lambda r: None, lambda n: None, 2)
app2.open_diary(); app2.open_append()
app2.search_s.update_nav({'pos': 2, 'total': 9, 'prev': True, 'next': True}); app2.search_s.update_nav(None)
print('листание OK')

# ---- окно «Синхронизация»
app2.sync_dialog('start', 'Идёт…')
assert app2._sync_pop is not None and app2._sync_parts[2].disabled
app2.sync_dialog('done', 'Готово')
assert not app2._sync_parts[2].disabled and app2._sync_parts[0].text == 'Готово'
assert app2._on_key(None, 27) is True
app2._sync_close()
assert app2._sync_pop is None
print('sync dialog OK')
