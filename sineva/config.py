"""mconfig.ini — тот же формат, что у Синевы+ для ПК, поэтому файл можно скопировать между ними.

[Settings]   default_separator, search_delay_ms, ... (+ мобильные: page_size, brief_view, candidate_limit)
[Bases]      имя_файла = Отображаемое имя, разделитель[, исключаемая подстрока Excel]
[VectorModels] имя = id, тип, путь   (разделы на ПК и на телефоне читаются одинаково)
"""
import configparser
import os
from typing import Dict, Tuple

DEFAULTS = {
    'default_separator': 'N1000',
    'search_delay_ms': '300',
    'page_size': '3',             # сколько карточек рисуется за раз
    'best_step': '20',            # «лучшие N» топиков; кнопка расширяет на столько же
    'brief_view': '0',            # 0 — топик целиком (по умолчанию); 1 — краткий вид (только предложения с совпадениями)
    'candidate_limit': '3000',    # сколько топиков из индекса проверяется на совпадения (экономия на телефоне)
    'min_query_len': '3',         # поиск стартует от стольких знаков
    'last_indices': '',           # выбранные для поиска книги (имена файлов через "|")
    'vector_models_dir': 'vector_models',
    'font_size': '16',            # размер шрифта результатов (щипок двумя пальцами)
    'picker_start': '',           # откуда открывать окно выбора файлов (запоминается)
    'theme': 'day',               # day — обычная, night — ночная (экономит заряд)
    'append_target': '',          # книга, которую пополняет кнопка «Дополнить» (запоминается)
}


def _new_parser() -> configparser.ConfigParser:
    p = configparser.ConfigParser(interpolation=None, delimiters=('=',), strict=False)
    p.optionxform = str          # регистр имён файлов сохраняется
    return p


class AppConfig:
    def __init__(self, path: str):
        self.path = path
        self.settings: Dict[str, str] = dict(DEFAULTS)
        self.bases: Dict[str, Tuple[str, str, str]] = {}      # файл -> (имя, разделитель, исключение)
        self._parser = _new_parser()
        self.load()

    # ---------- чтение ----------
    def load(self):
        self._parser = _new_parser()
        self.settings = dict(DEFAULTS)
        self.bases = {}
        if os.path.exists(self.path):
            for enc in ('utf-8-sig', 'cp1251'):
                try:
                    with open(self.path, encoding=enc) as f:
                        self._parser.read_file(f)
                    break
                except UnicodeDecodeError:
                    self._parser = _new_parser()
                except configparser.Error:
                    self._parser = _new_parser()
                    break
        if self._parser.has_section('Settings'):
            for k, v in self._parser.items('Settings'):
                self.settings[k] = v.strip()
        sep = self.settings.get('default_separator') or 'N1000'
        if self._parser.has_section('Bases'):
            for key, value in self._parser.items('Bases'):
                parts = [x.strip() for x in value.split(',')]
                if len(parts) >= 3:
                    self.bases[key] = (parts[0], parts[1], parts[2])
                elif len(parts) == 2:
                    self.bases[key] = (parts[0], parts[1], '')
                else:
                    self.bases[key] = (value.strip(), sep, '')

    # ---------- настройки ----------
    def get(self, key: str, default: str = '') -> str:
        return self.settings.get(key, default)

    def get_int(self, key: str, default: int) -> int:
        try:
            return int(self.settings.get(key, default))
        except (TypeError, ValueError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        v = str(self.settings.get(key, '1' if default else '0')).strip().lower()
        return v in ('1', 'true', 'yes', 'on', 'да')

    def set(self, key: str, value) -> None:
        self.settings[key] = str(value)

    # ---------- книги ----------
    def base_for(self, file_name: str) -> Tuple[str, str, str]:
        """(отображаемое имя, разделитель, исключение) — по умолчанию имя без расширения и общий разделитель."""
        entry = self.bases.get(file_name)
        if entry:
            display, sep, excl = entry
            return display or os.path.splitext(file_name)[0], sep or self.get('default_separator', 'N1000'), excl
        return os.path.splitext(file_name)[0], self.get('default_separator', 'N1000'), ''

    def set_base(self, file_name: str, display: str, separator: str, exclude: str = '') -> None:
        self.bases[file_name] = (display.strip(), separator.strip(), exclude.strip())

    def remove_base(self, file_name: str) -> None:
        self.bases.pop(file_name, None)

    # ---------- запись ----------
    def save(self) -> None:
        p = self._parser
        if not p.has_section('Settings'):
            p.add_section('Settings')
        for k, v in self.settings.items():
            p.set('Settings', k, str(v))
        if p.has_section('Bases'):
            p.remove_section('Bases')
        p.add_section('Bases')
        for key, (display, sep, excl) in self.bases.items():
            p.set('Bases', key, f"{display}, {sep}, {excl}" if excl else f"{display}, {sep}")
        os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            p.write(f)
        os.replace(tmp, self.path)
