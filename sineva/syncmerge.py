"""Синхронизация подчёркиваний, конспекта и дневника с папкой (Google Диск и т. п.). Чистая логика — тестируется на ПК.

Общий файл подчёркиваний sineva_marks.json (формат для телефона и десктопа):
  {"version": 1, "marks": [{"uid","file_key","file_label","fragment","before","after","created"}],
   "deleted": {"<uid>": "<время снятия ISO>"}}
uid = sha1(file_key \\x1f fragment \\x1f before \\x1f after)[:20] (см. marks.mark_uid).
Отметка жива, если нет следа о снятии или он старше её времени создания (подчеркнули заново — снова жива).

Конспект и дневник — файлы, куда только дописывают. Сливаются «по базе»: что дописано на каждой стороне после
прошлой синхронизации, то и добавляется (порядок: сначала чужое, потом своё). Если файл правили не только в конце —
берётся своя версия плюс чужие строки, которых в ней нет; обе версии сохраняются как .bak."""
import json
import os
from typing import Dict, List, Optional, Tuple

MARKS_FILE = 'sineva_marks.json'
TEXT_FILES = ('0_Конспект.txt', 'dnev.txt')


# ---------- подчёркивания ----------

def merge_marks(local_marks: List[dict], local_tombs: Dict[str, str], remote_json: Optional[bytes]):
    """-> (общий JSON для записи в папку, [отметки для добавления локально], [uid для удаления локально], новые следы)."""
    remote_marks, remote_tombs = [], {}
    if remote_json:
        try:
            data = json.loads(remote_json.decode('utf-8'))
            remote_marks = list(data.get('marks', []))
            remote_tombs = dict(data.get('deleted', {}))
        except Exception as e:
            raise ValueError(f"файл {MARKS_FILE} повреждён: {e}")
    tombs = dict(local_tombs)
    for uid, at in remote_tombs.items():
        if uid not in tombs or at > tombs[uid]:
            tombs[uid] = at
    local_uids = {m['uid'] for m in local_marks}
    merged: Dict[str, dict] = {}
    for m in list(remote_marks) + list(local_marks):
        old = merged.get(m['uid'])
        if old is None or (m.get('created') or '') > (old.get('created') or ''):
            merged[m['uid']] = m
    alive, dead = [], set()
    for uid, m in merged.items():
        t = tombs.get(uid)
        if t is not None and t >= (m.get('created') or ''):
            dead.add(uid)
        else:
            alive.append(m)
    tombs = {u: t for u, t in tombs.items() if u not in {m['uid'] for m in alive}}
    add = [m for m in alive if m['uid'] not in local_uids]
    remove = [u for u in local_uids if u in dead]
    alive.sort(key=lambda m: (m['file_key'], m.get('created') or '', m['uid']))
    out = json.dumps({'version': 1, 'marks': alive, 'deleted': tombs}, ensure_ascii=False, indent=1,
                     sort_keys=True).encode('utf-8')
    return out, add, remove, tombs


EXTRA_FILE = 'sineva_extra.json'      # размер, цвет и свои мысли: только мобильная версия (десктоп файл не читает)


def merge_extra(local_recs: List[dict], local_tombs: Dict[str, str], remote_json: Optional[bytes]):
    """Слияние слоёв (размер/цвет/мысли) по тому же правилу, что у подчёркиваний; у мысли побеждает более поздняя правка.
       -> (JSON для папки, [записи добавить], [uid убрать], общие следы)."""
    remote_recs, remote_tombs = [], {}
    if remote_json:
        try:
            data = json.loads(remote_json.decode('utf-8'))
            remote_recs, remote_tombs = list(data.get('records', [])), dict(data.get('deleted', {}))
        except Exception as e:
            raise ValueError(f"файл {EXTRA_FILE} повреждён: {e}")
    tombs = dict(local_tombs)
    for uid, at in remote_tombs.items():
        if uid not in tombs or at > tombs[uid]:
            tombs[uid] = at
    local_by = {m['uid']: m for m in local_recs}
    merged: Dict[str, dict] = {}
    for m in list(remote_recs) + list(local_recs):
        old = merged.get(m['uid'])
        if old is None or (m.get('created') or '') > (old.get('created') or ''):
            merged[m['uid']] = m
    alive, dead = [], set()
    for uid, m in merged.items():
        t = tombs.get(uid)
        if t is not None and t >= (m.get('created') or ''):
            dead.add(uid)
        else:
            alive.append(m)
    alive_uids = {m['uid'] for m in alive}
    tombs = {u: t for u, t in tombs.items() if u not in alive_uids}
    add, remove = [], [u for u in local_by if u in dead]
    for m in alive:
        mine = local_by.get(m['uid'])
        if mine is None:
            add.append(m)
        elif mine != m:                                   # чужая версия новее (например, исправленный текст мысли)
            remove.append(m['uid'])
            add.append(m)
    alive.sort(key=lambda m: (m.get('file_key', ''), m.get('created') or '', m['uid']))
    out = json.dumps({'version': 1, 'records': alive, 'deleted': tombs}, ensure_ascii=False, indent=1,
                     sort_keys=True).encode('utf-8')
    return out, add, remove, tombs


# ---------- тексты ----------

def _records(data: bytes) -> List[bytes]:
    """Записи файла: цитата конспекта («.......текст» + строка «(источник)») — одна запись, остальные строки — по одной."""
    out, cur = [], None
    for ln in data.split(b'\n'):
        ln = ln.rstrip(b'\r')
        if ln.startswith(b'.......'):
            if cur is not None:
                out.append(cur)
            cur = ln
        elif cur is not None and ln.startswith(b'(') and b'\n' not in cur[7:] and cur.count(b'\n') == 0:
            cur += b'\n' + ln
            out.append(cur)
            cur = None
        else:
            if cur is not None:
                out.append(cur)
                cur = None
            if ln.strip():
                out.append(ln)
    if cur is not None:
        out.append(cur)
    return out


def merge_text(local: bytes, remote: Optional[bytes], base: Optional[bytes]) -> Tuple[bytes, bool]:
    """-> (результат, нужна_резервная_копия). Результат записывается в обе стороны."""
    remote = remote or b''
    base = base or b''
    if local == remote:
        return local, False
    if not remote:
        return local, False
    if not local:
        return remote, False
    if base == b'' and not (local.startswith(remote) or remote.startswith(local)):
        base = None                                           # первая синхронизация двух разных файлов: без дублей по записям
    if base is not None and local.startswith(remote):
        return local, False
    if base is not None and remote.startswith(local) and base == b'':
        return remote, False
    if base is not None and local.startswith(base) and remote.startswith(base):
        mine, theirs = local[len(base):], remote[len(base):]
        if not mine:
            return remote, False
        if not theirs:
            return local, False
        if mine == theirs:
            return local, False
        sep = b'' if base.endswith(b'\n') or not base else b'\n'
        tail = b'' if theirs.endswith(b'\n') else b'\n'
        return base + sep + theirs + tail + mine, False
    # правка не только в конце: свой файл + чужие строки, которых в нём нет
    have, theirs_set = set(_records(local)), set(_records(remote))
    if have <= theirs_set:
        return remote, False                                  # свои записи уже есть в чужом файле (порядок — как там)
    extra = [c for c in _records(remote) if c not in have]
    if not extra:
        return local, True
    tail = b'' if local.endswith(b'\n') else b'\n'
    return local + tail + b'\n'.join(extra) + b'\n', True


# ---------- хранилища папки ----------

class DirFolder:
    """Обычная папка (ПК, тесты). У Android-версии тот же интерфейс — см. ui/safsync.py."""

    def __init__(self, path: str):
        self.path = path

    def read(self, name: str) -> Optional[bytes]:
        p = os.path.join(self.path, name)
        if not os.path.exists(p):
            return None
        with open(p, 'rb') as f:
            return f.read()

    def names(self):
        return sorted(n for n in os.listdir(self.path) if os.path.isfile(os.path.join(self.path, n)))

    def write(self, name: str, data: bytes) -> None:
        p = os.path.join(self.path, name)
        with open(p, 'wb') as f:
            f.write(data)


def sync_all(folder, marks_store, local_files: Dict[str, str], base_dir: str, log=lambda t: None) -> dict:
    """Один проход синхронизации. local_files: имя в папке -> локальный путь. Возвращает {'marks': +добавлено, -снято, 'texts': [имена изменённых локально]}."""
    os.makedirs(base_dir, exist_ok=True)
    report = {'marks_added': 0, 'marks_removed': 0, 'texts_changed': [], 'texts_pushed': []}
    # подчёркивания
    local_marks, local_tombs = marks_store.sync_snapshot()
    remote = folder.read(MARKS_FILE)
    out, add, remove, tombs = merge_marks(local_marks, local_tombs, remote)
    if remote and out != remote:
        folder.write(MARKS_FILE[:-5] + '.bak.json', remote)
    if add or remove or tombs != local_tombs:
        marks_store.sync_apply(add, remove, tombs)
    report['marks_added'], report['marks_removed'] = len(add), len(remove)
    if out != remote:
        folder.write(MARKS_FILE, out)
    # слои: размер, цвет, мысли (если хранилище их умеет)
    if hasattr(marks_store, 'extra_snapshot'):
        recs, etombs = marks_store.extra_snapshot()
        eremote = folder.read(EXTRA_FILE)
        eout, eadd, eremove, etombs2 = merge_extra(recs, etombs, eremote)
        if eremote and eout != eremote:
            folder.write(EXTRA_FILE[:-5] + '.bak.json', eremote)
        if eadd or eremove or etombs2 != etombs:
            marks_store.extra_apply(eadd, eremove, etombs2)
        report['extra_added'], report['extra_removed'] = len(eadd), len(eremove)
        if eout != eremote:
            folder.write(EXTRA_FILE, eout)
    # тексты
    for name, path in local_files.items():
        local = open(path, 'rb').read() if os.path.exists(path) else b''
        remote = folder.read(name)
        if not local and not remote:
            continue
        base_path = os.path.join(base_dir, name)
        base = open(base_path, 'rb').read() if os.path.exists(base_path) else None
        if base is None and local and remote and local != remote:
            base = b''                                       # первая синхронизация: оба файла считаются «дописанными»
        merged, need_bak = merge_text(local, remote, base)
        if not merged:
            continue                                         # пустым ничего не затираем ни здесь, ни в папке
        if need_bak:
            if local:
                with open(os.path.join(base_dir, name + '.local.bak'), 'wb') as f:
                    f.write(local)
            if remote:
                folder.write(name + '.bak', remote)
        if merged != local:
            tmp = path + '.part'
            with open(tmp, 'wb') as f:
                f.write(merged)
            os.replace(tmp, path)
            report['texts_changed'].append(name)
        if merged != remote:
            folder.write(name, merged)
            report['texts_pushed'].append(name)
        with open(base_path, 'wb') as f:
            f.write(merged)
    return report
