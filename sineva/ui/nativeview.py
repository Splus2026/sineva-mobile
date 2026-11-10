"""Родная отрисовка карточек: Android WebView поверх области списка (pyjnius). Только Android: на ПК available() = False.

WebView — родной виджет, поэтому текст не превращается в текстуры Kivy: вёрстка, прокрутка и подсветка делает Android.
Связь: Python → страница через evaluateJavascript(js); страница → Python опросом window.SV.take() (JSON-события)."""
import json

try:                                                         # только на Android
    from android.runnable import run_on_ui_thread            # type: ignore
    from jnius import PythonJavaClass, autoclass, java_method  # type: ignore
    ANDROID = True
except Exception:                                            # ПК и тесты
    ANDROID = False

    def run_on_ui_thread(fn):
        return fn

    class PythonJavaClass:                                   # заглушка
        pass

    def java_method(*a, **k):
        return lambda fn: fn


def available() -> bool:
    return ANDROID


class _ValueCallback(PythonJavaClass):
    __javainterfaces__ = ['android/webkit/ValueCallback']
    __javacontext__ = 'app'

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    @java_method('(Ljava/lang/Object;)V')
    def onReceiveValue(self, value):
        try:
            self.fn(None if value is None else str(value))
        except Exception:
            pass


class NativeList:
    """Один WebView со списком карточек. Размер и положение задаёт set_geometry (по виджету-заглушке Kivy)."""

    def __init__(self, page_html: str, on_events, background_rgb=(0, 0, 0)):
        self.page_html = page_html
        self.on_events = on_events                  # вызывается из потока Kivy со списком событий
        self.background = background_rgb
        self.view = None
        self.ready = False
        self.queue = []
        self.geom = None
        self.visible = False
        self._cb = _ValueCallback(self._on_poll)
        self._create()

    # ---- создание/удаление (поток интерфейса Android)
    @run_on_ui_thread
    def _create(self):
        act = autoclass('org.kivy.android.PythonActivity').mActivity
        WebView = autoclass('android.webkit.WebView')
        Params = autoclass('android.widget.FrameLayout$LayoutParams')
        wv = WebView(act)
        wv.getSettings().setJavaScriptEnabled(True)
        wv.getSettings().setDomStorageEnabled(False)
        r, g, b = self.background
        wv.setBackgroundColor(self._argb(r, g, b))
        wv.setVerticalScrollBarEnabled(True)
        wv.setVisibility(8)                                   # GONE, пока нет геометрии
        wv.loadDataWithBaseURL(None, self.page_html, 'text/html', 'utf-8', None)
        act.addContentView(wv, Params(10, 10))
        self.view = wv

    @staticmethod
    def _argb(r, g, b):
        v = (255 << 24) | (int(r * 255) << 16) | (int(g * 255) << 8) | int(b * 255)
        return v - (1 << 32) if v >= (1 << 31) else v

    @run_on_ui_thread
    def destroy(self):
        wv, self.view = self.view, None
        if wv is not None:
            parent = wv.getParent()
            if parent is not None:
                parent.removeView(wv)
            wv.destroy()

    # ---- положение и видимость
    @run_on_ui_thread
    def _apply(self, geom, visible):
        wv = self.view
        if wv is None or geom is None:
            return
        Params = autoclass('android.widget.FrameLayout$LayoutParams')
        x, y, w, h = geom
        lp = Params(max(1, w), max(1, h))
        lp.gravity = 51                                       # TOP | LEFT
        lp.leftMargin, lp.topMargin = x, y
        wv.setLayoutParams(lp)
        wv.setVisibility(0 if visible else 8)

    def sync(self, geom, visible):
        """geom = (left, top, width, height) в пикселях окна; вызывается периодически, шлёт на Android только изменения."""
        geom = tuple(int(v) for v in geom)
        if geom != self.geom or visible != self.visible:
            self.geom, self.visible = geom, bool(visible)
            self._apply(self.geom, self.visible)

    # ---- команды в страницу и события из неё
    @run_on_ui_thread
    def _eval(self, js, cb=None):
        if self.view is not None:
            self.view.evaluateJavascript(js, cb)

    def call(self, js: str):
        if self.ready:
            self._eval(js)
        else:
            self.queue.append(js)                              # страница ещё грузится

    def poll(self):
        """Вызывать ~6 раз в секунду: забирает события страницы."""
        self._eval("window.SV?window.SV.take():null", self._cb)

    def _on_poll(self, raw):
        if not raw or raw == 'null':
            return
        if not self.ready:
            self.ready = True
            for js in self.queue:
                self._eval(js)
            self.queue.clear()
        try:
            events = json.loads(json.loads(raw))
        except Exception:
            return
        if events:
            self._deliver(events)

    def _deliver(self, events):
        from kivy.clock import mainthread
        mainthread(lambda: self.on_events(events))()
