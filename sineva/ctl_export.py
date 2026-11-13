"""Пароль, экспорт, «поделиться», «Дополнить» книгу."""
import os
import shutil
import time
from typing import Optional
from . import marks as marks_mod
from . import notes
from .splitter import split_into_sections_by_separator
from .textio import read_text_file


class ExportMixin:
    """Пароль, экспорт, «поделиться», «Дополнить» книгу. Методы смешиваются в Controller."""

    # ================= пароль =================
    @staticmethod
    def _hash(password: str, salt: str) -> str:
        import hashlib
        return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), 100000).hex()

    def has_password(self) -> bool:
        return bool(self.config.get('pwd_hash', ''))

    def set_password(self, password: str) -> None:
        if not password:
            self.set_setting('pwd_hash', '')
            self.set_setting('pwd_salt', '')
            return
        salt = os.urandom(16).hex()
        self.set_setting('pwd_salt', salt)
        self.set_setting('pwd_hash', self._hash(password, salt))

    def check_password(self, password: str) -> bool:
        if not self.has_password():
            return True
        return self._hash(password, self.config.get('pwd_salt', '')) == self.config.get('pwd_hash', '')

    def quote_to_conspect(self, key: str, text: str) -> None:
        """«В конспект»: цитата + источник — в файл 0_Конспект.txt (формат как на ПК)."""
        item = self._find_item(key)
        source = self._source_label(item['book']) if item else ''
        try:
            notes.append_quote(self.paths.conspect_path, text, source)
            self.on_status("Сохранено в конспект")
        except Exception as e:
            self.on_status(f"Не удалось записать в конспект: {e}")

    # ================= экспорт, «поделиться», «Дополнить» =================
    def export_marks(self, fmt: str = 'txt') -> Optional[str]:
        """Файл со всеми подчёркнутыми фрагментами в exports/. None — подчёркиваний ещё нет."""
        marks = self.marks.all_marks()
        if not marks:
            self.on_status("Подчёркнутых фрагментов пока нет")
            return None
        path = self._export_name('Подчёркнутое', fmt)
        try:
            (marks_mod.export_marks_docx if fmt == 'docx' else marks_mod.export_marks_txt)(marks, path)
        except Exception as e:
            self.on_status(f"Не удалось сохранить: {e}")
            return None
        self.on_status(f"Сохранено: {path}")
        return path

    def export_notes(self) -> Optional[str]:
        """Файл «Мысли_…txt» со всеми своими мыслями. None — мыслей ещё нет."""
        notes_ = self.marks.all_notes()
        if not notes_:
            self.on_status("Своих мыслей пока нет")
            return None
        path = self._export_name('Мысли', 'txt')
        try:
            marks_mod.export_notes_txt(notes_, path)
        except Exception as e:
            self.on_status(f"Не удалось сохранить: {e}")
            return None
        self.on_status(f"Сохранено: {os.path.basename(path)}")
        return path

    def _export_name(self, label: str, ext: str) -> str:
        """Имя вида «Конспект_2026-11-08_12-30-15.txt»: пункт меню + дата-время экспорта."""
        return os.path.join(self.paths.exports_dir, f"{label}_{time.strftime('%Y-%m-%d_%H-%M-%S')}.{ext}")

    def export_conspect(self) -> Optional[str]:
        return self._export_copy(self.conspect_file(), 'Конспект')

    def export_diary(self) -> Optional[str]:
        return self._export_copy(self.diary_file(), 'Дневник')

    def _export_copy(self, src: Optional[str], label: str) -> Optional[str]:
        if not src:
            return None
        dst = self._export_name(label, 'txt')
        try:
            shutil.copyfile(src, dst)
        except Exception as e:
            self.on_status(f"Не удалось сохранить: {e}")
            return None
        self.on_status(f"Сохранено: {os.path.basename(dst)}")
        return dst

    def list_exports(self) -> list:
        """Экспортированные файлы: [(имя, путь, время, размер)], последние сверху."""
        out = []
        try:
            for n in os.listdir(self.paths.exports_dir):
                p = os.path.join(self.paths.exports_dir, n)
                if os.path.isfile(p):
                    st = os.stat(p)
                    out.append((n, p, st.st_mtime, st.st_size))
        except OSError:
            pass
        out.sort(key=lambda x: x[2], reverse=True)
        return out

    def conspect_file(self) -> Optional[str]:
        if os.path.exists(self.paths.conspect_path) and os.path.getsize(self.paths.conspect_path) > 0:
            return self.paths.conspect_path
        self.on_status("Конспект пока пуст")
        return None

    def append_target(self) -> str:
        """Книга для «Дополнить»: запомненная, если она ещё есть в списке."""
        name = self.config.get('append_target', '')
        names = [b.file_name for b in self.library.books]
        return name if name in names else ''

    def add_to_diary(self, text: str) -> bool:
        """«В дневник»: текст дописывается в dnev.txt (папка книг), книга переиндексируется и попадает в поиск."""
        if not text.strip():
            return False
        path = self.paths.diary_path
        try:
            if not os.path.exists(path):
                open(path, 'wb').close()
            notes.append_to_book(path, text)
        except Exception as e:
            self.on_status(f"Не удалось записать в дневник: {e}")
            return False
        self.library.scan()
        self.publish_books()
        self.on_status("Записано в дневник.")
        self.maybe_index([os.path.basename(path)])
        return True

    def diary_file(self) -> Optional[str]:
        path = self.paths.diary_path
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return path
        self.on_status("Дневник пока пуст")
        return None

    def last_block(self, file_name: str, max_chars: int = 700) -> str:
        """Последний топик книги (по её разделителю) — показывается над полем ввода в «Дополнить»."""
        book = self.library.find(file_name)
        if book is None:
            return ''
        try:
            sections = split_into_sections_by_separator(read_text_file(book.path).splitlines(), book.separator)
        except Exception as e:
            return f"(не удалось прочитать: {e})"
        if not sections:
            return ''
        content = '\n'.join(sections[-1]['lines']).strip()
        return ('… ' + content[-max_chars:]) if len(content) > max_chars else content

    def diary_tail(self, max_chars: int = 3000) -> str:
        """Конец дневника (последние записи) — показывается в верхней части окна «Дневник»."""
        path = self.paths.diary_path
        if not os.path.exists(path):
            return ''
        try:
            text = read_text_file(path).rstrip()
        except Exception as e:
            return f"(не удалось прочитать: {e})"
        return ('… ' + text[-max_chars:]) if len(text) > max_chars else text

    def append_to_book(self, file_name: str, text: str) -> bool:
        book = self.library.find(file_name)
        if book is None:
            self.on_status("Книга не найдена")
            return False
        try:
            notes.append_to_book(book.path, text)
        except Exception as e:
            self.on_status(f"Не удалось дополнить: {e}")
            return False
        self.config.set('append_target', file_name)       # выбор запоминается
        self.config.save()
        self.library.scan()
        self.publish_books()
        self.on_status(f"Добавлено в «{book.display}».")
        self.maybe_index([file_name])
        return True
