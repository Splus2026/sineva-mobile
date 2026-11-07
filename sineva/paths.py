"""Где лежат файлы приложения: на Android и на ПК (для разработки и тестов)."""
import os


def is_android() -> bool:
    return 'ANDROID_ARGUMENT' in os.environ or 'ANDROID_ROOT' in os.environ and 'ANDROID_DATA' in os.environ


class AppPaths:
    """books_dir   — книги (.txt/.docx). На Android это папка приложения во внешней памяти
                    (Android/data/<пакет>/files/books): её видно при подключении к ПК по USB.
       index_dir   — индексы (SQLite), во внутренней памяти приложения.
       config_path — mconfig.ini (формат тот же, что у Синевы+ для ПК)."""

    def __init__(self, root: str = None, books_dir: str = None):
        if root is None:
            root = self._default_root()
        self.root = root
        self.books_dir = books_dir or self._default_books_dir(root)
        self.index_dir = os.path.join(root, 'indices')
        self.config_path = os.path.join(root, 'mconfig.ini')
        self.vector_dir = os.path.join(root, 'vector_models')
        for d in (self.root, self.books_dir, self.index_dir, self.vector_dir):
            os.makedirs(d, exist_ok=True)

    @staticmethod
    def _default_root() -> str:
        if is_android():
            try:
                from android.storage import app_storage_path  # type: ignore
                return app_storage_path()
            except Exception:
                pass
        return os.path.join(os.path.expanduser('~'), '.sineva_mobile')

    @staticmethod
    def _default_books_dir(root: str) -> str:
        if is_android():
            try:  # папка приложения во внешней памяти: доступна по USB, права не нужны
                from jnius import autoclass  # type: ignore
                activity = autoclass('org.kivy.android.PythonActivity').mActivity
                ext = activity.getExternalFilesDir(None)
                if ext is not None:
                    return os.path.join(ext.getAbsolutePath(), 'books')
            except Exception:
                pass
        return os.path.join(root, 'books')
