"""Выбор файлов-книг: на Android — системный выбор (SAF) через androidstorage4kivy, на ПК — окно Kivy."""
import os
import shutil

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
    dst = os.path.join(dest_dir, name)
    try:
        # основной путь: файловый дескриптор документа читается обычными средствами Python
        pfd = resolver.openFileDescriptor(parsed, 'r')
        if pfd is None:
            raise RuntimeError("нет доступа к файлу")
        fd = pfd.detachFd()
        with os.fdopen(fd, 'rb') as src, open(dst, 'wb') as out:
            shutil.copyfileobj(src, out, 1 << 20)
    except Exception:
        stream = resolver.openInputStream(parsed)
        if stream is None:
            raise RuntimeError(f"не открывается {name}")
        try:
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
    if os.path.getsize(dst) == 0:
        os.remove(dst)
        raise RuntimeError(f"{name}: получен пустой файл (файл в облаке? скачайте его на телефон)")
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


_SHARED = {}              # локальный путь -> (uri, видимый путь): файл копируется в «Документы» один раз


def _mime(path):
    ext = os.path.splitext(path)[1].lower()
    return {'.txt': 'text/plain', '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}.get(
        ext, '*/*')


def publish_shared(path):
    """Android: копия файла в общую папку «Документы/<название приложения>» (видна в «Файлах» и доступна другим
       приложениям). Возвращает (uri, видимый_путь). На ПК и при ошибке — исключение/None."""
    if path in _SHARED:
        return _SHARED[path]
    from androidstorage4kivy import SharedStorage      # type: ignore
    from jnius import autoclass                         # type: ignore
    uri = SharedStorage().copy_to_shared(path)
    ctx = autoclass('org.kivy.android.PythonActivity').mActivity
    try:
        label = ctx.getApplicationInfo().loadLabel(ctx.getPackageManager()).toString()
    except Exception:
        label = 'Синева Mobile'
    shown = f"Documents/{label}/{os.path.basename(path)}"
    _SHARED[path] = (uri, shown)
    return _SHARED[path]


def share_file(path, status=lambda text: None):
    """«Поделиться файлом»: файл копируется в «Документы» и открывается системное меню отправки
       (мессенджеры, почта, диск). На ПК просто сообщает путь."""
    if not is_android():
        status(f"Файл: {path}")
        return
    uri, shown = None, None
    try:
        uri, shown = publish_shared(path)
    except Exception as e:
        status(f"Не удалось скопировать в «Документы»: {e}. Файл: {path}")
    if uri is not None:
        try:
            from jnius import autoclass, cast           # type: ignore
            Intent = autoclass('android.content.Intent')
            String = autoclass('java.lang.String')
            act = autoclass('org.kivy.android.PythonActivity').mActivity
            intent = Intent(Intent.ACTION_SEND)
            intent.setType(_mime(path))
            intent.putExtra(Intent.EXTRA_STREAM, cast('android.os.Parcelable', uri))
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            act.startActivity(Intent.createChooser(intent, cast('java.lang.CharSequence', String('Отправить файл'))))
            status(f"Файл: {shown}")
            return
        except Exception as e:
            status(f"Меню отправки не открылось ({e}). Файл сохранён: {shown}")
    try:                                                # запасной путь: стандартный ShareSheet библиотеки
        from androidstorage4kivy import ShareSheet     # type: ignore
        ShareSheet().share_file(path)
    except Exception:
        pass


def add_home_shortcut(status=lambda text: None):
    """Просит систему поставить ярлык приложения на главный экран (Android 8+; система покажет подтверждение).
       Без этого значок виден только в списке приложений — Android сам ярлыки на рабочий стол не создаёт."""
    if not is_android():
        status("Ярлык создаётся только на Android")
        return
    try:
        from jnius import autoclass, cast              # type: ignore
        PythonActivity = autoclass('org.kivy.android.PythonActivity')
        ctx = PythonActivity.mActivity
        Build = autoclass('android.os.Build$VERSION')
        if Build.SDK_INT < 26:
            status("На этой версии Android ярлык добавляется вручную: долгое нажатие на значок → «Добавить на главный экран»")
            return
        Context = autoclass('android.content.Context')
        ShortcutInfoBuilder = autoclass('android.content.pm.ShortcutInfo$Builder')
        Icon = autoclass('android.graphics.drawable.Icon')
        context = cast('android.content.Context', ctx)
        # getSystemService — по имени службы (строкой): перегрузка по классу путает pyjnius
        manager = cast('android.content.pm.ShortcutManager', context.getSystemService(Context.SHORTCUT_SERVICE))
        if manager is None or not manager.isRequestPinShortcutSupported():
            status("Лаунчер не поддерживает автоматический ярлык: долгое нажатие на значок приложения → «Добавить на главный экран»")
            return
        intent = context.getPackageManager().getLaunchIntentForPackage(context.getPackageName())
        intent.setAction('android.intent.action.MAIN')
        info = (ShortcutInfoBuilder(context, 'sineva_main')
                .setShortLabel('Синева Mobile')
                .setIcon(Icon.createWithResource(context, context.getApplicationInfo().icon))
                .setIntent(intent)
                .build())
        manager.requestPinShortcut(info, None)
        status("Подтвердите добавление ярлыка в окне системы")
    except Exception as e:
        status(f"Не удалось создать ярлык: {e}")
