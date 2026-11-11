"""Android: папка синхронизации (Google Диск и др.) через системный доступ к папке (SAF, ACTION_OPEN_DOCUMENT_TREE).
Разрешение сохраняется навсегда (persistable), поэтому папку достаточно выбрать один раз."""
import os

REQUEST_CODE = 7002


def available() -> bool:
    return 'ANDROID_ARGUMENT' in os.environ or ('ANDROID_ROOT' in os.environ and 'ANDROID_DATA' in os.environ)


def pick_folder(on_uri, on_error=lambda t: None):
    """Открывает системное окно выбора папки; on_uri(uri:str) — в потоке Kivy."""
    from android import activity                       # type: ignore
    from jnius import autoclass                        # type: ignore
    from kivy.clock import Clock

    Intent = autoclass('android.content.Intent')
    PythonActivity = autoclass('org.kivy.android.PythonActivity')
    intent = Intent(Intent.ACTION_OPEN_DOCUMENT_TREE)
    intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION
                    | Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)

    def on_result(request_code, result_code, data):
        if request_code != REQUEST_CODE:
            return
        activity.unbind(on_activity_result=on_result)
        if result_code != -1 or data is None or data.getData() is None:
            return
        uri = data.getData()
        try:
            PythonActivity.mActivity.getContentResolver().takePersistableUriPermission(
                uri, Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
        except Exception as e:
            on_error(f"Не удалось сохранить доступ к папке: {e}")
        Clock.schedule_once(lambda dt: on_uri(uri.toString()), 0)

    activity.bind(on_activity_result=on_result)
    PythonActivity.mActivity.startActivityForResult(intent, REQUEST_CODE)


class SafFolder:
    """Тот же интерфейс, что у syncmerge.DirFolder: read(name) -> bytes|None, write(name, data)."""

    def __init__(self, tree_uri: str):
        from jnius import autoclass                    # type: ignore
        self.Uri = autoclass('android.net.Uri')
        self.DC = autoclass('android.provider.DocumentsContract')
        self.resolver = autoclass('org.kivy.android.PythonActivity').mActivity.getContentResolver()
        self.tree = self.Uri.parse(tree_uri)
        self.root_id = self.DC.getTreeDocumentId(self.tree)
        self.root_doc = self.DC.buildDocumentUriUsingTree(self.tree, self.root_id)

    def label(self) -> str:
        """Имя папки для показа в настройках (у Google Диска идентификатор длинный — берём отображаемое имя)."""
        try:
            cur = self.resolver.query(self.root_doc, ['_display_name'], None, None, None)
            try:
                if cur is not None and cur.moveToFirst() and cur.getString(0):
                    return cur.getString(0)
            finally:
                if cur is not None:
                    cur.close()
        except Exception:
            pass
        return 'выбрана'

    def _find(self, name: str):
        children = self.DC.buildChildDocumentsUriUsingTree(self.tree, self.root_id)
        cur = self.resolver.query(children, ['document_id', '_display_name'], None, None, None)
        if cur is None:
            raise RuntimeError("папка недоступна")
        try:
            while cur.moveToNext():
                if cur.getString(1) == name:
                    return self.DC.buildDocumentUriUsingTree(self.tree, cur.getString(0))
        finally:
            cur.close()
        return None

    def names(self):
        """Имена файлов папки (подпапки пропускаются)."""
        children = self.DC.buildChildDocumentsUriUsingTree(self.tree, self.root_id)
        cur = self.resolver.query(children, ['document_id', '_display_name', 'mime_type'], None, None, None)
        if cur is None:
            raise RuntimeError("папка недоступна")
        out = []
        try:
            while cur.moveToNext():
                if cur.getString(2) != 'vnd.android.document/directory' and cur.getString(1):
                    out.append(cur.getString(1))
        finally:
            cur.close()
        return sorted(out)

    def read(self, name: str):
        uri = self._find(name)
        if uri is None:
            return None
        pfd = self.resolver.openFileDescriptor(uri, 'r')
        if pfd is None:
            raise RuntimeError(f"{name}: нет доступа (файл в облаке? откройте его в приложении «Диск»)")
        try:
            fd = os.dup(pfd.getFd())
            with os.fdopen(fd, 'rb') as f:
                return f.read()
        finally:
            pfd.close()

    def write(self, name: str, data: bytes) -> None:
        uri = self._find(name)
        if uri is None:
            mime = 'application/json' if name.endswith('.json') else 'text/plain'
            uri = self.DC.createDocument(self.resolver, self.root_doc, mime, name)
            if uri is None:
                raise RuntimeError(f"не удалось создать {name}")
        # Поток обязательно закрывается штатно (close): только так поставщик (Google Диск) узнаёт, что запись окончена
        out = self.resolver.openOutputStream(uri, 'wt')
        if out is None:
            raise RuntimeError(f"{name}: нет доступа на запись")
        try:
            buf = bytearray(data)
            step = 65536
            for i in range(0, len(buf), step):
                part = buf[i:i + step]
                out.write(part, 0, len(part))
            out.flush()
        finally:
            out.close()
