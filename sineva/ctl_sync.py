"""Синхронизация с папкой (Google Диск)."""
import os
import shutil
import time
from . import syncmerge


class SyncMixin:
    """Синхронизация с папкой (Google Диск). Методы смешиваются в Controller."""

    # ================= синхронизация с папкой (Google Диск) =================
    def sync_uri(self) -> str:
        return self.config.get('sync_uri', '')

    def sync_now(self, folder=None) -> None:
        """Ручная синхронизация подчёркиваний, конспекта и дневника с папкой. folder — для тестов (DirFolder)."""
        if folder is None:
            uri = self.sync_uri()
            if not uri:
                self.on_status("Сначала выберите папку синхронизации")
                return
            try:
                from .ui.safsync import SafFolder
                folder = SafFolder(uri)
            except Exception as e:
                self.on_status(f"Папка синхронизации недоступна: {e}")
                if self.on_sync:
                    self.on_sync('error', f"Папка синхронизации недоступна:\n{e}")
                return
        if getattr(self, '_syncing', False):
            return
        self._syncing = True
        self.on_status("Синхронизация…")
        if self.on_sync:
            self.on_sync('start', "Идёт синхронизация…\nПодождите, не закрывайте приложение.")
        files = {'0_Конспект.txt': self.paths.conspect_path, 'dnev.txt': self.paths.diary_path}

        def work():
            try:
                rep = syncmerge.sync_all(folder, self.marks, files, os.path.join(self.paths.root, 'sync_base'))
                err = None
            except Exception as e:
                rep, err = None, e

            def done():
                self._syncing = False
                if err is not None:
                    self.on_status(f"Синхронизация не удалась: {err}")
                    if self.on_sync:
                        self.on_sync('error', f"Синхронизация не удалась:\n{err}")
                    return
                if rep['marks_added'] or rep['marks_removed']:
                    self.reset_views()                          # карточки на экране устарели
                if rep['texts_changed']:
                    self.library.scan()
                    self.publish_books()
                    if 'dnev.txt' in rep['texts_changed']:
                        self.maybe_index(['dnev.txt'])
                msg = (f"Синхронизация завершена.\nПодчёркиваний: +{rep['marks_added']}, −{rep['marks_removed']}\n"
                       f"Файлов обновлено на телефоне: {len(rep['texts_changed'])}, в папке: {len(rep['texts_pushed'])}")
                self.on_status(msg.replace('\n', ' · '))
                if self.on_sync:
                    self.on_sync('done', msg)
            self.ui_post(done)
        self._run(work)

    # ---------- архив папки синхронизации (аварийное восстановление) ----------
    save_download = None            # Android: функция, кладущая файл в системную папку «Загрузки»; по умолчанию ~/Downloads

    def _sync_folder(self, folder=None):
        if folder is not None:
            return folder
        uri = self.sync_uri()
        if not uri:
            raise RuntimeError("Сначала выберите папку синхронизации")
        from .ui.safsync import SafFolder
        return SafFolder(uri)

    def backup_sync_folder(self, cb=None, folder=None) -> None:
        """Все файлы папки синхронизации -> один zip в «Загрузках» (не удаляется при переустановке приложения).
           cb(ok, текст) вызывается в потоке интерфейса."""
        cb = cb or (lambda ok, text: None)
        try:
            folder = self._sync_folder(folder)
        except Exception as e:
            cb(False, f"Архив не создан:\n{e}")
            return

        def work():
            import zipfile
            tmp, ok, msg = None, False, ''
            try:
                names = folder.names()
                if not names:
                    raise RuntimeError("в папке синхронизации нет файлов")
                os.makedirs(self.paths.exports_dir, exist_ok=True)
                tmp = os.path.join(self.paths.exports_dir, f"Синева_папка_синхронизации_{time.strftime('%Y-%m-%d_%H-%M-%S')}.zip")
                count = 0
                with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
                    for n in names:
                        data = folder.read(n)
                        if data is not None:
                            z.writestr(n, data)
                            count += 1
                saver = self.save_download or self._save_download_default
                shown = saver(tmp)
                ok, msg = True, f"Архив готов: {shown}\nФайлов в архиве: {count}"
            except Exception as e:
                msg = f"Архив не создан:\n{e}"
            finally:
                if tmp and os.path.exists(tmp):
                    try:
                        os.remove(tmp)                      # копия осталась в «Загрузках»
                    except OSError:
                        pass
            self.ui_post(lambda: cb(ok, msg))
        self._run(work)

    def _save_download_default(self, path: str) -> str:
        import shutil
        d = os.path.join(os.path.expanduser('~'), 'Downloads')
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, os.path.basename(path))
        shutil.copyfile(path, dst)
        return dst
