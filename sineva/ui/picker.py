"""Выбор файлов-книг: на Android — системный выбор (SAF) через androidstorage4kivy, на ПК — окно Kivy."""
import os

from ..paths import is_android


DEFAULT_START = "content://com.android.externalstorage.documents/document/primary%3ADocuments"
REQUEST_CODE = 7001


def pick_files(on_paths, start_uri='', remember=lambda uri: None, dest_dir=None, on_error=lambda text: None):
    """on_paths(list[str]) вызывается с путями файлов, уже доступных приложению для чтения.
       start_uri — откуда открыть окно (по умолчанию «Документы»); remember(uri) — запомнить место последнего выбора."""
    if is_android():
        try:
            _pick_android(on_paths, start_uri or DEFAULT_START, remember, dest_dir, on_error)
            return
        except Exception:
            pass
        try:                                     # запасной путь: стандартный выбор из androidstorage4kivy
            from androidstorage4kivy import Chooser, SharedStorage
            holder = {}

            def cb(uris):
                ss = SharedStorage()
                paths = []
                for u in uris or []:
                    try:
                        p = ss.copy_from_shared(u)
                        if p:
                            paths.append(p)
                    except Exception:
                        pass
                on_paths(paths)
            holder['chooser'] = Chooser(cb)
            holder['chooser'].choose_content('*/*', multiple=True)
            pick_files._holder = holder
            return
        except Exception:
            pass
    _desktop_chooser(on_paths)


def copy_uri(uri, dest_dir):
    """Копирует выбранный документ прямо в папку книг под его настоящим именем (с расширением)."""
    from jnius import autoclass                        # type: ignore
    Uri = autoclass('android.net.Uri')
    PythonActivity = autoclass('org.kivy.android.PythonActivity')
    resolver = PythonActivity.mActivity.getContentResolver()
    parsed = Uri.parse(uri)
    name = None
    cursor = resolver.query(parsed, None, None, None, None)
    if cursor is not None:
        try:
            idx = cursor.getColumnIndex('_display_name')
            if idx >= 0 and cursor.moveToFirst():
                name = cursor.getString(idx)
        finally:
            cursor.close()
    if not name:
        name = os.path.basename(Uri.decode(uri)) or 'book.txt'
    name = name.replace('/', '_')
    stream = resolver.openInputStream(parsed)
    if stream is None:
        raise RuntimeError(f"не открывается {name}")
    dst = os.path.join(dest_dir, name)
    try:
        Files = autoclass('java.nio.file.Files')
        Paths = autoclass('java.nio.file.Paths')
        Options = autoclass('java.nio.file.StandardCopyOption')
        Files.copy(stream, Paths.get(dst), [Options.REPLACE_EXISTING])
    except Exception:
        # Android 7 (API 24-25): без java.nio.file — копируем блоками через массив байт Java
        Array = autoclass('java.lang.reflect.Array')
        buf = Array.newInstance(autoclass('java.lang.Byte').TYPE, 65536)
        with open(dst, 'wb') as out:
            while True:
                n = stream.read(buf)
                if n <= 0:
                    break
                out.write(bytes((b & 0xFF) for b in buf[:n]))
    finally:
        stream.close()
    return dst


def _pick_android(on_paths, start_uri, remember, dest_dir, on_error):
    """Системное окно выбора документов (SAF), открытое сразу в нужной папке (EXTRA_INITIAL_URI, Android 8+)."""
    from android import activity                       # type: ignore
    from jnius import autoclass, cast                  # type: ignore
    from kivy.clock import Clock

    Intent = autoclass('android.content.Intent')
    Uri = autoclass('android.net.Uri')
    PythonActivity = autoclass('org.kivy.android.PythonActivity')

    intent = Intent(Intent.ACTION_OPEN_DOCUMENT)
    intent.addCategory(Intent.CATEGORY_OPENABLE)
    intent.setType('*/*')
    intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, True)
    try:
        intent.putExtra('android.provider.extra.INITIAL_URI',
                        cast('android.os.Parcelable', Uri.parse(start_uri)))
    except Exception:
        pass                                           # без стартовой папки окно откроется как обычно

    def on_result(request_code, result_code, data):
        if request_code != REQUEST_CODE:
            return
        activity.unbind(on_activity_result=on_result)
        if result_code != -1 or data is None:         # -1 == RESULT_OK
            return
        uris = []
        clip = data.getClipData()
        if clip is not None:
            for i in range(clip.getItemCount()):
                uris.append(clip.getItemAt(i).getUri().toString())
        elif data.getData() is not None:
            uris.append(data.getData().toString())
        if not uris:
            return

        def finish(dt):
            remember(uris[0])                          # следующий раз окно откроется там же
            paths, errors = [], []
            for u in uris:
                try:
                    paths.append(copy_uri(u, dest_dir))
                except Exception as e:
                    errors.append(str(e))
            if errors:
                on_error("Не удалось скопировать: " + "; ".join(errors))
            on_paths(paths)
        Clock.schedule_once(finish, 0)

    activity.bind(on_activity_result=on_result)
    PythonActivity.mActivity.startActivityForResult(intent, REQUEST_CODE)


def _desktop_chooser(on_paths):
    from kivy.uix.boxlayout import BoxLayout
    from kivy.uix.button import Button
    from kivy.uix.filechooser import FileChooserListView
    from kivy.uix.popup import Popup

    box = BoxLayout(orientation='vertical')
    fc = FileChooserListView(path=os.path.expanduser('~'), multiselect=True,
                             filters=['*.txt', '*.docx', '*.TXT', '*.DOCX'])
    row = BoxLayout(size_hint_y=None, height='48dp')
    ok, cancel = Button(text='Добавить'), Button(text='Отмена')
    row.add_widget(ok)
    row.add_widget(cancel)
    box.add_widget(fc)
    box.add_widget(row)
    pop = Popup(title='Выберите книги', content=box, size_hint=(0.95, 0.9))
    ok.bind(on_release=lambda *_: (pop.dismiss(), on_paths(list(fc.selection))))
    cancel.bind(on_release=lambda *_: pop.dismiss())
    pop.open()


def share_file(path, status=lambda text: None):
    """«Поделиться файлом»: на Android — системное меню отправки (мессенджер, почта, диск);
       если оно недоступно — копия в общую папку «Документы». На ПК просто сообщает путь."""
    if is_android():
        try:
            from androidstorage4kivy import ShareSheet
            ShareSheet().share_file(path)
            return
        except Exception:
            pass
        try:
            from androidstorage4kivy import SharedStorage
            SharedStorage().copy_to_shared(path)
            status(f"Файл скопирован в общую папку «Документы»: {os.path.basename(path)}")
            return
        except Exception as e:
            status(f"Не удалось поделиться: {e}. Файл: {path}")
            return
    status(f"Файл: {path}")


def add_home_shortcut(status=lambda text: None):
    """Просит систему поставить ярлык приложения на главный экран (Android 8+; система покажет подтверждение).
       Без этого значок виден только в списке приложений — Android сам ярлыки на рабочий стол не создаёт."""
    if not is_android():
        status("Ярлык создаётся только на Android")
        return
    try:
        from jnius import autoclass                    # type: ignore
        PythonActivity = autoclass('org.kivy.android.PythonActivity')
        ctx = PythonActivity.mActivity
        Build = autoclass('android.os.Build$VERSION')
        if Build.SDK_INT < 26:
            status("На этой версии Android ярлык добавляется вручную: долгое нажатие на значок → «Добавить на главный экран»")
            return
        ShortcutManager = autoclass('android.content.pm.ShortcutManager')
        ShortcutInfoBuilder = autoclass('android.content.pm.ShortcutInfo$Builder')
        Icon = autoclass('android.graphics.drawable.Icon')
        manager = ctx.getSystemService(ShortcutManager)
        if manager is None or not manager.isRequestPinShortcutSupported():
            status("Лаунчер не поддерживает автоматический ярлык: долгое нажатие на значок приложения → «Добавить на главный экран»")
            return
        intent = ctx.getPackageManager().getLaunchIntentForPackage(ctx.getPackageName())
        intent.setAction('android.intent.action.MAIN')
        info = (ShortcutInfoBuilder(ctx, 'sineva_main')
                .setShortLabel('Синева+')
                .setIcon(Icon.createWithResource(ctx, ctx.getApplicationInfo().icon))
                .setIntent(intent)
                .build())
        manager.requestPinShortcut(info, None)
        status("Подтвердите добавление ярлыка в окне системы")
    except Exception as e:
        status(f"Не удалось создать ярлык: {e}")
