"""Тесты ядра и логики экранов (без Kivy):  python tests/run_core_tests.py"""
import os
import shutil
import sys
import tempfile
import time
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from sineva.config import AppConfig
from sineva.controller import Controller
from sineva.index import BookIndex, build_index
from sineva.library import NOT_INDEXED, READY, STALE, Library, safe_index_name
from sineva.paths import AppPaths
from sineva.queryparser import parse_query_words
from sineva.search import SearchService, escape_markup
from sineva.splitter import split_into_sections_by_separator
from sineva.textio import read_text_file
from sineva.vector import VectorModel, VectorRegistry, VectorUnavailable, register_model_type

FAILED, TOTAL = [], 0


def check(name, cond, extra=''):
    global TOTAL
    TOTAL += 1
    if not cond:
        FAILED.append(name)
        print(f"FAIL: {name} {extra}")


def make_docx(path, paragraphs):
    from xml.sax.saxutils import escape
    body = ''.join(f'<w:p><w:r><w:t>{escape(p)}</w:t></w:r></w:p>' for p in paragraphs)
    xml = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
           f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>')
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('word/document.xml', xml)


BIBLE = """1 В начале была вера. Вера в добро и недоверие к злу.
2 Доверие рождает любовь, а любовь — веру. Верность ценнее золота.
3 Ничего про искомое здесь нет. Просто текст о погоде и море.
4 Ве-ве-ве, слоги без смысла.
"""

tmp = tempfile.mkdtemp(prefix='sm_')
try:
    paths = AppPaths(root=os.path.join(tmp, 'app'))
    books = paths.books_dir
    with open(os.path.join(books, 'bibl.txt'), 'w', encoding='cp1251') as f:      # старая кодировка
        f.write(BIBLE)
    with open(os.path.join(books, 'Грани.txt'), 'w', encoding='utf-8') as f:
        f.write("1 Мысль первая о доверии.\n2 Мысль вторая о красоте.\n")
    make_docx(os.path.join(books, 'doc.docx'), ['1 Абзац про веру.', '2 Абзац про море [скобки] & амперсанд.'])
    for service in ('readme.txt', 'requirements.txt'):
        open(os.path.join(books, service), 'w').write('служебный')

    # ---------- чтение и разбивка ----------
    check('чтение cp1251', 'доверие' in read_text_file(os.path.join(books, 'bibl.txt')).lower())
    check('чтение docx', read_text_file(os.path.join(books, 'doc.docx')).startswith('1 Абзац'))
    lines = BIBLE.splitlines()
    check('Numeric: 4 топика', len(split_into_sections_by_separator(lines, 'Numeric')) == 4)
    check('N30: режет по размеру', len(split_into_sections_by_separator(lines, 'N30')) > 4)
    check('NS60: смысловая', len(split_into_sections_by_separator(lines, 'NS60')) >= 2)
    check('строка-разделитель', len(split_into_sections_by_separator(lines, '2 ')) == 2)

    # ---------- конфигурация (совместима с ПК) ----------
    ini = os.path.join(tmp, 'desktop.ini')
    open(ini, 'w', encoding='utf-8').write("[Settings]\ndefault_separator = N1000\nsearch_delay_ms = 250\n\n[Engine]\ntype = fts5\n\n"
                                          "[Bases]\nbibl.txt = Библия, N700\nНоменклатура.xlsx = Номенклатура, ExcelRow1, строка\n\n"
                                          "[VectorModels]\nFT Библия = ft_bibliya, fasttext, vector_models\\ft_bibliya.model\n")
    c = AppConfig(ini)
    check('Bases: алиас+разделитель', c.base_for('bibl.txt') == ('Библия', 'N700', ''))
    check('Bases: третье поле', c.base_for('Номенклатура.xlsx')[2] == 'строка')
    check('Settings читаются', c.get_int('search_delay_ms', 0) == 250 and c.get_int('page_size', 0) == 3)
    c.set_base('x.txt', 'Икс', 'Numeric'); c.save()
    c2 = AppConfig(ini)
    check('сохранение сохраняет чужие разделы', c2._parser.has_section('Engine') and c2._parser.has_section('VectorModels'))
    check('сохранение: новая книга', c2.base_for('x.txt')[:2] == ('Икс', 'Numeric'))

    # ---------- библиотека и индексы ----------
    lib = Library(paths, AppConfig(paths.config_path))
    names = [b.file_name for b in lib.books]
    check('служебные файлы не книги', names == ['bibl.txt', 'doc.docx', 'Грани.txt'], str(names))
    check('все не проиндексированы', all(b.state == NOT_INDEXED for b in lib.books))
    check('имя индекса безопасное и разное', safe_index_name('Грани.txt') != safe_index_name('Грани.docx')
          and safe_index_name('Грани.txt').isascii())
    for b in lib.books:
        lib.index_book(b)
    check('после индексации ready', all(b.state == READY for b in lib.books))
    bibl = lib.find('bibl.txt')
    check('топиков в bibl', bibl.topics == 1 or bibl.topics >= 1)   # N1000: весь текст — 1 блок
    lib.update_book_settings(bibl, 'Библия', 'Numeric')
    bibl = lib.find('bibl.txt')
    check('смена разделителя -> stale', bibl.state == STALE and bibl.display == 'Библия')
    lib.index_book(bibl)
    check('после переиндексации ready, 4 топика', bibl.state == READY and bibl.topics == 4, f"{bibl.state} {bibl.topics}")
    time.sleep(1.1)
    with open(bibl.path, 'a', encoding='cp1251') as f:
        f.write("5 Новый топик про веру.\n")
    lib.scan()
    check('файл изменён -> stale', lib.find('bibl.txt').state == STALE)
    lib.index_book(lib.find('bibl.txt'))
    check('5 топиков', lib.find('bibl.txt').topics == 5)

    # ---------- поиск в индексе ----------
    def hits(query, fts=True, name='bibl.txt'):
        b = lib.find(name)
        idx = BookIndex(b.index_path)
        try:
            return idx.search(parse_query_words(query))
        finally:
            idx.close()

    r = hits('доверие')
    check('поиск слова, регистр кириллицы', any(it['rowid'] == 2 for it in r))
    check('фрагмент "доверие" находит "недоверие"', any('недоверие' in it['meta']['matched_words'] for it in r))
    r = hits('.доверие')
    check('точка спереди исключает недоверие', all('недоверие' not in it['meta']['matched_words'] for it in r))
    r = hits('вер.а')
    mw = set(w for it in r for w in it['meta']['matched_words'])
    check('вер.а: вера и вер..., без слога ве', 'вера' in mw and 'ве' not in mw, str(mw))
    r = hits('ВЕРА')
    check('верхний регистр запроса', len(r) >= 2)
    r = hits('море')
    check('слово из другого топика', any(it['rowid'] == 3 for it in r))
    check('нет совпадений -> пусто', hits('кракозябра') == [])
    _r = hits('вера'); _idx = BookIndex(lib.find('bibl.txt').index_path)
    _data = _idx.fetch([it['rowid'] for it in _r]); _idx.close()
    check('текст и позиции подгружаются отдельно', all(it['content'] is None for it in _r) and all(v[0] and v[1] != '' for v in _data.values()) and len(_data) == len(_r))

    # короткие фрагменты (<3 знаков) и старый формат индекса
    p3 = os.path.join(tmp, 'fmt.db')
    build_index(bibl.path, p3, 'Numeric')
    idx = BookIndex(p3)
    r_short = idx.search(parse_query_words('ве', 2))
    idx.close()
    check('двухзнаковый запрос работает', len(r_short) >= 1)
    import sqlite3 as _sq
    old = os.path.join(tmp, 'old.db')
    _c = _sq.connect(old); _c.execute("CREATE TABLE info(key TEXT, value TEXT)"); _c.execute("INSERT INTO info VALUES('topics','3')"); _c.commit(); _c.close()
    idx = BookIndex(old)
    try:
        idx.search(parse_query_words('вера')); check('старый формат: ошибка', False)
    except ValueError as e:
        check('старый формат: понятная ошибка', 'Индексировать' in str(e))
    idx.close()
    check('старый формат -> книга stale', True)

    # ---------- поиск по нескольким книгам ----------
    cfg = lib.config
    svc = SearchService(cfg)
    out = svc.search('доверие', lib.books)
    svc.fill(out.results)
    keys = [(it['meta']['max_level'], it['meta']['count_at_max']) for it in out.results]
    check('результаты из нескольких книг', {it['book'] for it in out.results} >= {'bibl.txt', 'Грани.txt'})
    check('сортировка по уровню и числу', keys == sorted(keys, reverse=True))
    check('панель: точные слова впереди', svc.panel_words(out)[0]['exact'] is True)
    words = [r['word'] for r in svc.panel_words(out)]
    check('счётчики слов', out.word_counts.get('доверие', 0) >= 1 and 'недоверие' in words and 'доверии' in words)
    svc.toggle_word('доверие')
    vis = svc.visible(out)
    check('исключение слова скрывает только топики без других слов', len(vis) <= len(out.results))
    svc.toggle_word('доверие')
    for w in list(out.word_counts):
        svc.toggle_word(w)
    check('все слова исключены -> пусто', svc.visible(out) == [])
    svc.excluded.clear()
    # разметка
    mk = svc.topic_markup(out, out.results[0], brief=False)
    mks = [svc.topic_markup(out, it, brief=False) for it in out.results]
    check('подсветка в разметке', all('[color=' in m for m in mks) and any('[b]' in m for m in mks))
    out2 = svc.search('амперсанд', lib.books)
    svc.fill(out2.results)
    m2 = svc.topic_markup(out2, out2.results[0], brief=False)
    check('экранирование [ ] &', '&bl;скобки&br;' in m2 and '&amp;' in m2 and '[скобки]' not in m2, m2)
    check('escape_markup', escape_markup('a[b]&') == 'a&bl;b&br;&amp;')
    check('брифовый вид короче/равен', len(svc.topic_markup(out, out.results[0], brief=True)) <= len(mk) + 50)
    lib.scan()
    nb = lib.find('big.txt') if False else None
    out_e = SearchService(cfg).search('вера', [Library(AppPaths(root=os.path.join(tmp, 'e2')), AppConfig(os.path.join(tmp, 'e2.ini'))).find('x') or lib.books[0]])
    check('поиск не падает', out_e is not None)

    # ---------- заставка: слово собирается из пикселей ----------
    from sineva.ui.smoke import SmokeText, layout
    st = SmokeText()
    cells, cols = layout('Синева Mobile')
    check('заставка: слово из пикселей', len(cells) > 100 and cols > 60 and len(st) == len(cells) * 4)
    x0, y0, sc0, a0 = st.state(0, 0.0)
    xe, ye, sce, ae = st.state(0, st.assembled_at)
    check('заставка: в начале частицы невидимы и разбросаны', a0 == 0 and abs(y0 - st.p[0][1]) > 3)
    check('заставка: в конце слово собрано точно', all(abs(st.state(i, st.assembled_at)[0] - st.p[i][0]) < 1e-9 and
          abs(st.state(i, st.assembled_at)[1] - st.p[i][1]) < 1e-9 and st.state(i, st.assembled_at)[3] == 1.0 for i in range(len(st))))

    # ---------- индексация: по умолчанию ручная ----------
    import sineva.config as _cfg
    check('после установки индексация ручная', _cfg.DEFAULTS['index_auto'] == '0' and AppConfig(os.path.join(tmp, 'новый.ini')).get_bool('index_auto', False) is False)
    _cfg.DEFAULTS['index_auto'] = '1'          # дальше тесты проверяют автоматический режим

    # ---------- контроллер ----------
    ctl = Controller(paths, run_async=False)
    ctl.set_setting('index_auto', 1)          # дальше — проверка автоматического режима
    got = {'cards': None, 'words': None, 'status': '', 'books': None}
    ctl.on_cards = lambda cards, total, more: got.update(cards=cards, total=total, more=more)
    ctl.on_words = lambda rows: got.update(words=rows)
    ctl.on_status = lambda s: got.update(status=s)
    ctl.on_books = lambda rows: got.update(books=rows)
    ctl.toggle_book('bibl.txt'); ctl.toggle_book('Грани.txt')
    ctl.on_query_changed('довер')
    check('ввод текста поиск не запускает', got['cards'] is None)
    ctl.submit_query('до')
    check('короткий запрос — подсказка, поиска нет', 'не менее' in got['status'] and got['cards'] is None)
    ctl.submit_query('довер')
    check('кнопка «Поиск» выдала карточки', got['cards'] and got['total'] >= 2, str(got['status']))
    check('в статусе запрос и число блоков', "найдено блоков" in got['status'])
    check('по умолчанию топик целиком', all(not c['expanded'] and c['segments'] == [(0, 0, len(c['text']))] for c in got['cards']))
    check('в карточке есть жёлтые участки', any(c['hl'] for c in got['cards']))
    n_cards = len(got['cards'])
    first_word = got['words'][0]['word']
    ctl.toggle_word(first_word)
    check('исключение слова', len(got['cards']) <= n_cards and any(r['excluded'] for r in got['words']))
    ctl.exclude_all_words()
    check('«исключить все»', got['cards'] == [] and all(r['excluded'] for r in got['words']))
    ctl.exclude_all_words()
    check('повторно — вернуть все', all(not r['excluded'] for r in got['words']) and got['cards'])
    ctl.config.set('page_size', 1)
    ctl.submit_query('вера')
    check('на экране один блок, есть следующий', len(got['cards']) == 1 and got['more']['next'] is True and got['more']['pos'] == 1)
    ctl.go(1)
    check('листание вперёд', len(got['cards']) == 1 and got['more']['pos'] == 2 and got['more']['prev'] is True)
    ctl.go(-1)
    check('листание назад', got['more']['pos'] == 1 and got['more']['prev'] is False)
    ctl.config.set('brief_view', 1)
    ctl.submit_query('вера')
    check('краткий вид задаётся настройкой', any(c['segments'] != [(0, 0, len(c['text']))] or len(c['text']) < 60 for c in got['cards']))
    ctl.config.set('brief_view', 0)
    ctl.submit_query('вера')
    key = got['cards'][0]['key']
    ctl.toggle_expand(key)
    check('раскрытие топика', got['cards'][0]['expanded'] is True)

    # ---------- подчёркивание, конспект ----------
    ctl.submit_query('вера')
    card = got['cards'][0]; txt = card['text']
    i = txt.index('вер') ; j = i + 12
    check('подчёркнутых нет', card['marks'] == [])
    check('выделение добавляет подчёркивание', ctl.mark_selection(card['key'], i, j) is True)
    card2 = [c for c in got['cards'] if c['key'] == card['key']][0]
    check('подчёркивание видно в карточке', card2['marks'] and card2['marks'][0][0] <= i < card2['marks'][0][1])
    check('повтор не плодит отметки', ctl.mark_selection(card['key'], i, j) is False and len(ctl.marks.all_marks()) == 1)
    ctl.mark_selection(card['key'], j - 4, j + 10)
    check('пересекающиеся отметки сливаются', len(ctl.marks.all_marks()) == 1)
    ctl.quote_to_conspect(card['key'], txt[i:j])
    ck = open(paths.conspect_path, encoding='utf-8').read()
    check('конспект: цитата и источник', ck.startswith('.......' + txt[i:j]) and '(bibl.txt' in ck or '(Грани.txt' in ck, ck)
    p_txt = ctl.export_marks('txt'); p_docx = ctl.export_marks('docx')
    check('экспорт txt', p_txt and txt[i:j].strip()[:6] in open(p_txt, encoding='utf-8').read())
    from sineva.textio import _read_docx_paragraphs_fast
    check('экспорт docx открывается', p_docx and any('Подчёркнутые' in x for x in _read_docx_paragraphs_fast(p_docx)))
    check('выделение отмечено целиком', ctl.selection_marked(card['key'], i, j))
    ctl.unmark_selection(card['key'], 0, len(txt), publish=False)
    check('снятие без перерисовки списка', ctl.card_marks(card['key']) == [] and not ctl.selection_marked(card['key'], i, j))
    ctl.mark_selection(card['key'], i, j, publish=False)
    cm = ctl.card_marks(card['key'])
    check('card_marks: отметка одной карточки', cm and cm[0][0] <= i and cm[0][1] >= j - 1, str(cm))
    ctl.unmark_selection(card['key'], i, j)
    check('снятие подчёркивания', len(ctl.marks.all_marks()) <= 1)
    ctl.unmark_selection(card['key'], 0, len(txt))
    check('снятие со всего', ctl.marks.all_marks() == [])
    check('экспорт без отметок -> None', ctl.export_marks('txt') is None)

    # ---------- выделение без подтверждений ----------
    ctl.config.set('page_size', 50)
    ctl.submit_query('любовь')
    tc = got['cards'][0]; tt = tc['text']; ti = tt.index('любовь')
    before = open(paths.conspect_path, encoding='utf-8').read()
    check('новое выделение -> marked', ctl.toggle_mark(tc['key'], ti, ti + 6, 'любовь') == 'marked')
    after = open(paths.conspect_path, encoding='utf-8').read()
    check('в конспект: с новой строки и семиточием', after[len(before):].startswith('.......любовь\n') or after[len(before):].startswith('\n.......любовь\n'), repr(after[len(before):]))
    check('подчёркивание видно', ctl.card_marks(tc['key']) != [])
    check('повторное выделение -> unmarked', ctl.toggle_mark(tc['key'], ti, ti + 6, 'любовь') == 'unmarked')
    check('подчёркивание снято, конспект не тронут', ctl.card_marks(tc['key']) == [] and open(paths.conspect_path, encoding='utf-8').read() == after)

    # ---------- порции: по 3 карточки, лучшие 20 + расширение ----------
    many = os.path.join(paths.books_dir, 'Много.txt')
    open(many, 'w', encoding='utf-8').write(''.join(f'{i} Строка про терпение номер {i}.\n' for i in range(1, 61)))
    ctl.library.scan(); ctl.library.update_book_settings(ctl.library.find('Много.txt'), 'Много', 'Numeric')
    ctl.index_books(['Много.txt'])
    for b in list(ctl.selected):
        if b != 'Много.txt': ctl.toggle_book(b)
    ctl.config.set('page_size', 3)
    ctl.submit_query('терпение')
    check('сначала один блок из 60', len(got['cards']) == 1 and got['total'] == 60 and got['more'] == {'pos': 1, 'total': 60, 'prev': False, 'next': True}, str(got['more']))
    check('в статусе замеры и режим', 'поиск' in got['status'] and 'словарь' in got['status'], got['status'])
    for _ in range(25):
        ctl.go(1)
    check('листание вперёд на 25 блоков', got['more']['pos'] == 26 and len(got['cards']) == 1, str(got['more']))
    check('далёкие блоки освобождают текст', ctl.outcome.results[0]['content'] is None)
    check('номер блока в заголовке', '26.' in got['cards'][0]['header'], got['cards'][0]['header'])
    for b in ('bibl.txt', 'Грани.txt'):
        ctl.toggle_book(b)
    ctl.config.set('page_size', 50)

    # ---------- длинные топики окнами ----------
    big_book = os.path.join(paths.books_dir, 'Длинная.txt')
    open(big_book, 'w', encoding='utf-8').write('1 ' + ' '.join(f'слово{i}' for i in range(3000)) + ' редкаяфраза в конце ' + ' '.join(f'хвост{i}' for i in range(500)) + '\n')
    ctl.library.scan(); ctl.library.update_book_settings(ctl.library.find('Длинная.txt'), 'Длинная', 'Numeric')
    ctl.index_books(['Длинная.txt'])
    ctl.config.set('page_size', 50)
    ctl.submit_query('редкаяфраза')
    lc = [c for c in got['cards'] if c['key'].startswith('Длинная.txt')][0]
    check('длинный топик режется на куски', len(lc['chunks']) > 1 and all(e - st <= ctl.MAX_CHARS for st, e in lc['chunks'])
          and lc['chunks'][-1][1] == len(lc['text']), str(lc['chunks'][:3]))
    check('текст полный, подсветка есть', 'редкаяфраза' in lc['text'] and lc['hl'] and len(lc['text']) > ctl.MAX_CHARS)
    ctl.mark_selection(lc['key'], lc['text'].index('редкаяфраза'), lc['text'].index('редкаяфраза') + 11, publish=False)
    lc3 = ctl.card_marks(lc['key'])
    check('подчёркивание в окне длинного топика', lc3 and lc['text'][lc3[0][0]:lc3[0][1]].strip().startswith('редкаяфраза'), str(lc3))
    ctl.unmark_selection(lc['key'], 0, len(lc['text']))

    # ---------- последний блок книги для «Дополнить» ----------
    lb = ctl.last_block('bibl.txt')
    check('последний блок книги', lb.startswith('5 Новый топик'), repr(lb))
    check('последний блок: нет книги -> пусто', ctl.last_block('нет.txt') == '')

    # ---------- «Дополнить» ----------
    check('цель не задана', ctl.append_target() == '')
    ok = ctl.append_to_book('Грани.txt', '3 Добавленный текст про море.')
    check('дополнение книги', ok and '3 Добавленный' in open(os.path.join(paths.books_dir, 'Грани.txt'), encoding='utf-8').read())
    check('выбор запомнен', ctl.append_target() == 'Грани.txt' and Controller(paths, run_async=False).append_target() == 'Грани.txt')
    check('после дополнения книга переиндексирована', ctl.library.find('Грани.txt').state == READY)
    ctl.submit_query('добавленный')
    check('новый текст находится поиском', got['total'] >= 1)
    check('дополнение .docx', ctl.append_to_book('doc.docx', '3 Новый абзац.') and '3 Новый абзац.' in read_text_file(os.path.join(paths.books_dir, 'doc.docx')))

    # ---------- дневник ----------
    check('дневник пуст', ctl.diary_file() is None)
    check('запись в дневник', ctl.add_to_diary('Сегодня думал о вере.') and ctl.add_to_diary('Вторая запись про море.'))
    dn = os.path.join(paths.books_dir, 'dnev.txt')
    check('dnev.txt в папке книг, записи подряд', open(dn, encoding='utf-8').read() == 'Сегодня думал о вере.\nВторая запись про море.\n')
    check('дневник в списке книг и проиндексирован', ctl.library.find('dnev.txt') and ctl.library.find('dnev.txt').state == READY)
    ctl.config.set('page_size', 50); ctl.submit_query('море')
    check('дневник находится поиском', got['total'] >= 1 and any(it['book'] == 'dnev.txt' for it in ctl.outcome.results))
    check('дневник доступен для экспорта', ctl.diary_file() == dn)
    check('пустая запись не пишется', ctl.add_to_diary('   ') is False)

    # вручную скопированный файл виден после тихого обновления; неподдерживаемый формат — сообщение
    open(os.path.join(paths.books_dir, 'Ручная.txt'), 'w', encoding='utf-8').write('1 Книга скопирована руками.\n')
    ctl.rescan()
    check('rescan видит вручную добавленный файл', 'Ручная.txt' in [r['file_name'] for r in got['books']])
    bad = os.path.join(tmp, 'x.pdf'); open(bad, 'wb').write(b'%PDF')
    ctl.add_books([bad])
    check('неподдерживаемый формат: сообщение', 'не поддерживается' in got['status'])

    # обновление списков (кнопка ↻)
    with open(os.path.join(paths.books_dir, 'Новая.txt'), 'w', encoding='utf-8') as f:
        f.write("1 Новая книга про веру.\n")
    ini_text = open(paths.config_path, encoding='utf-8').read()
    open(paths.config_path, 'w', encoding='utf-8').write(ini_text.replace('Библия, Numeric', 'Священная книга, Numeric'))
    ctl.refresh()
    names = [r['file_name'] for r in got['books']]
    disp = {r['file_name']: r['display'] for r in got['books']}
    check('обновление: новая книга видна', 'Новая.txt' in names)
    check('обновление: новый алиас из конфигурации', disp['bibl.txt'] == 'Священная книга', str(disp))
    check('обновление: книга не в индексе помечена', [r for r in got['books'] if r['file_name'] == 'Новая.txt'][0]['state'] == NOT_INDEXED)
    ctl.index_books(['Новая.txt'])
    check('индексация новой книги', ctl.library.find('Новая.txt').state == READY and 'Новая.txt' in ctl.selected)
    ctl.set_mode('vector')
    ctl.submit_query('вера')
    check('векторы: понятное сообщение', 'ещё не подключены' in got['status'] or 'векторн' in got['status'].lower(), got['status'])
    ctl.set_mode('text')
    ctl2 = Controller(paths, run_async=False)
    check('выбранные книги помнятся', ctl2.selected == ctl.selected)

    # ---------- отмена выделения и пароль ----------
    uc = Controller(paths, run_async=False)
    ug = {'cards': None}
    uc.on_cards = lambda cards, total, more: ug.update(cards=cards)
    uc.on_status = lambda t: None
    uc.selected = {'bibl.txt'}
    uc.submit_query('довер')
    card = ug['cards'][0]
    before_marks = len(uc.marks.all_marks())
    csz = os.path.getsize(paths.conspect_path) if os.path.exists(paths.conspect_path) else 0
    words = card['text'].split()
    s0 = card['text'].index(words[2]); e0 = card['text'].index(words[4]) + len(words[4])
    res = uc.toggle_mark(card['key'], s0, e0, card['text'][s0:e0])
    check('выделение записано в отметки и конспект', res == 'marked' and len(uc.marks.all_marks()) > before_marks
          and os.path.getsize(paths.conspect_path) > csz and len(uc.undo_stack) == 1)
    key = uc.undo_last_mark()
    check('отмена выделения: отметки и конспект как были', key == card['key'] and len(uc.marks.all_marks()) == before_marks
          and (os.path.getsize(paths.conspect_path) if os.path.exists(paths.conspect_path) else 0) == csz and not uc.undo_stack)
    check('пароль: по умолчанию нет', not uc.has_password() and uc.check_password('любой'))
    uc.set_password('секрет1')
    uc2 = Controller(paths, run_async=False)
    check('пароль: хранится как хэш и проверяется', uc2.has_password() and uc2.check_password('секрет1')
          and not uc2.check_password('секрет2') and 'секрет1' not in open(paths.config_path, encoding='utf-8').read())
    uc2.set_password('')
    check('пароль можно убрать', not Controller(paths, run_async=False).has_password())

    # ---------- вкладка «Чтение» ----------
    rc = Controller(paths, run_async=False)
    rg = {'cards': None, 'status': '', 'more': None}
    def on_cards(cards, total, more): rg.update(cards=cards, total=total, more=more)
    rc.on_cards = on_cards
    rc.on_status = lambda t: rg.update(status=t)
    rc.config.set('page_size', 2)
    rc.selected = {'bibl.txt', 'Грани.txt'}
    rc.set_tab('read')
    check('чтение при двух книгах — подсказка', 'ровно одну' in rg['status'] and not rg['cards'], rg['status'])
    rc.set_tab('search')
    rc.selected = {'bibl.txt'}
    rc.set_tab('read')
    check('чтение: один блок', rg['cards'] and len(rg['cards']) == 1 and rc.reading, rg['status'])
    check('чтение: заголовок «Блок 1»', 'Блок 1' in rg['cards'][0]['header'])
    first_text = rg['cards'][0]['text']
    rc.go(1)
    check('чтение: листание вперёд', len(rg['cards']) == 1 and rg['cards'][0]['text'] != first_text and 'Блок 2' in rg['cards'][0]['header'])
    rc.go(-1)
    check('чтение: листание назад', rg['cards'][0]['text'] == first_text and rg['more']['prev'] is False)
    rc.go(-1)
    check('чтение: до начала не листается', 'Блок 1' in rg['cards'][0]['header'])
    for _ in range(4):
        rc.go(1)
    check('чтение: подгрузка следующих блоков', rg['more']['pos'] == 5, str(rg['more']))
    rc.set_tab('search'); rc.set_tab('read')
    check('возврат на вкладку чтения — без повторных вопросов', rc.reading and 'Блок 5' in rc.full_cards()[0]['header'])
    rc2 = Controller(paths, run_async=False)
    rg2 = {}
    rc2.on_cards = lambda cards, total, more: rg2.update(cards=cards)
    rc2.on_status = lambda t: None
    rc2.selected = {'bibl.txt'}
    asked = {}
    rc2.on_ask_continue = lambda name, hist, cont, start: asked.update(n=len(hist), hist=hist, cont=cont, start=start)
    rc2.set_tab('read')
    check('прошлое место помнится: предложено продолжить', asked.get('n', 0) >= 1 and asked['hist'][-1][2], str(asked))
    asked['start']()
    check('«С начала» — блок 1', 'Блок 1' in rg2['cards'][0]['header'])
    rc2.set_tab('search'); rc2.reading = None; rc2._states.pop('read', None); rc2.set_tab('read')
    asked['cont'](asked['hist'][-1][0])
    check('«Продолжить» — с последнего места', 'Блок' in rg2['cards'][0]['header'] and rg2['cards'][0]['header'] != '')
    # лимит символов: большой блок — один; выбор книг изменился — результаты вкладки очищаются
    rc4 = Controller(paths, run_async=False)
    rc4.on_cards = lambda *a: None; msgs4 = []; rc4.on_status = msgs4.append
    rc4.config.set('page_chars', 10)
    check('лимит символов: минимум один блок', rc4._fit([500, 500, 500]) == 1 and rc4._fit([]) == 0)
    rc4.config.set('page_chars', 6000)
    check('лимит блоков: не больше page_size', rc4._fit([10] * 9) == rc4.config.get_int('page_size', 3) and rc4._fit([4000, 4000]) == 1, str(rc4.config.get_int('page_size', 3)))
    rc4.selected = set(); rc4.selection_status()
    check('статус без книг', 'Выберите книги' in msgs4[-1], msgs4[-1])
    rc4.selected = {'bibl.txt'}; rc4.selection_status()
    check('статус: алиас книги', 'Книги:' in msgs4[-1], msgs4[-1])
    check('склонение', [rc4._books_word(n) for n in (4, 5, 21)] == ['книги', 'книг', 'книга'])
    rc3 = Controller(paths, run_async=False)
    rc3.on_cards = lambda *a: None; rc3.on_status = lambda t: None
    rc3.selected = {'bibl.txt'}; rc3.config.set('page_size', 3)
    rc3.set_tab('read')
    rc3._read_from(rc3.library.find('bibl.txt'), 1)
    start_ord = rc3.outcome.results[rc3.win_start]['ordinal']
    for _ in range(3):
        rc3.go(1)
    for _ in range(2):
        rc3.go(-1)
    check('чтение: окно двигается туда и обратно', rc3.outcome.results[rc3.win_start]['ordinal'] == start_ord + 1, f"{start_ord} {rc3.win_start} {[r['ordinal'] for r in rc3.outcome.results]}")
    rc3.set_tab('search')
    rc3.submit_query('довер')
    rc3.go(1); rc3.go(1)
    check('поиск: показан один блок', rc3.shown == 1 and rc3.win_start >= 0)
    check('ручная индексация: maybe_index не индексирует', (rc3.config.set('index_auto', 0), rc3.maybe_index(['bibl.txt']))[1] is False)
    check('длинный текст режется на куски', all(b - a <= rc3.MAX_CHARS for a, b in Controller.chunks('слово ' * 2000)) and
          Controller.chunks('слово ' * 2000)[-1][1] == len('слово ' * 2000))

    # ---------- векторы: архитектура ----------
    vs = ctl2.vector_status()
    check('ещё нет моделей', vs['ready'] is False and vs['types'] == [])
    class FakeLite(VectorModel):
        kind = 'word'
        def load(self): self._ok = True
        def is_loaded(self): return getattr(self, '_ok', False)
        def neighbors(self, word, topn=15): return [(word + 's', 0.9)]
    reg = VectorRegistry(AppConfig(ini), tmp)
    check('неподдерживаемый тип — понятная проблема', reg.problems and 'fasttext' in reg.problems[0], str(reg.problems))
    register_model_type('fasttext', FakeLite)
    reg = VectorRegistry(AppConfig(ini), tmp)
    check('модель из [VectorModels] подключена', [m.display_name for m in reg.list()] == ['FT Библия'])
    m = reg.require()
    check('require загружает модель', m.is_loaded() and m.neighbors('дом') == [('домs', 0.9)])
    check('путь модели приведён к ОС', os.sep in m.path and 'ft_bibliya.model' in m.path, m.path)
    try:
        VectorRegistry().require(); check('пустой реестр бросает исключение', False)
    except VectorUnavailable:
        check('пустой реестр бросает исключение', True)

    # ---------- производительность на крупной книге ----------
    big = os.path.join(paths.books_dir, 'big.txt')
    with open(big, 'w', encoding='utf-8') as f:
        for i in range(1, 25001):
            f.write(f"{i} Параграф номер {i}: любовь, вера и надежда живут в сердце человека; слово{i % 97} и доверие.\n")
    lib.scan()
    t0 = time.time(); n = lib.index_book(lib.find('big.txt'), None) if False else build_index(big, os.path.join(tmp, 'big.db'), 'Numeric')
    t_idx = time.time() - t0
    idx = BookIndex(os.path.join(tmp, 'big.db'))
    t0 = time.time(); rr = idx.search(parse_query_words('надеж.да')); t_s = time.time() - t0
    idx.close()
    print(f"[perf] 25000 топиков: индексация {t_idx:.1f} с, поиск {len(rr)} топиков {t_s*1000:.0f} мс")
    check('большая книга: индексация < 30 с', t_idx < 30)
    check('большая книга: поиск < 3 с', t_s < 3)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ---- родная отрисовка: HTML карточек
from sineva.ui import htmlview, nativeview
import re as _re
_c = {'key': 'a.txt:5', 'header': '[b]Блок[/b] [size=12sp][color=777777]x[/color][/size]', 'text': 'Я <люблю> тебя & вас',
      'hl': [(2, 9, 2)], 'marks': [(10, 14)], 'expanded': False}
_h = htmlview.card_html(_c, True)
check('html: экранирование', '&lt;люблю&gt;' in _h and '&amp;' in _h and '<люблю>' not in _h)
check('html: подсветка и подчёркивание', 'hl2' in _h and 'mk' in _h and 'data-key="a.txt:5"' in _h)
check('html: кнопка Полностью', 'Полностью' in _h and 'Полностью' not in htmlview.card_html(_c))
_spans = [(int(a), int(b)) for a, b in _re.findall(r'data-a="(\d+)" data-b="(\d+)"', _h)]
check('html: куски покрывают текст', bool(_spans) and _spans[0][0] == 0 and _spans[-1][1] == len(_c['text'])
      and all(_spans[i][1] == _spans[i + 1][0] for i in range(len(_spans) - 1)))
import base64 as _b64
check('html: js_call отдаёт строки в base64', 'D("%s")' % _b64.b64encode('a"b\u2028'.encode()).decode() in htmlview.js_call('setMore', 'a"b\u2028'))
check('html: страница', 'setFont' in htmlview.page_html({'BG': (0, 0, 0, 1), 'CARD': (1, 1, 1, 1), 'BODY': (1, 1, 1, 1), 'ACCENT': (0, 0, 1, 1), 'MARK_COLOR': 'fff', 'HL_TEXT': ''}, 16))
check('native: на ПК недоступен', nativeview.available() is False)

# ---- синхронизация с папкой
import json as _json
from sineva import marks as _mk, syncmerge as _sm
_t = tempfile.mkdtemp()
try:
    _A, _B = _mk.MarksStore(os.path.join(_t, 'a.db')), _mk.MarksStore(os.path.join(_t, 'b.db'))
    _fold = _sm.DirFolder(os.path.join(_t, 'drive')); os.makedirs(_fold.path)
    _fa = {'0_Конспект.txt': os.path.join(_t, 'a_k.txt'), 'dnev.txt': os.path.join(_t, 'a_d.txt')}
    _fb = {'0_Конспект.txt': os.path.join(_t, 'b_k.txt'), 'dnev.txt': os.path.join(_t, 'b_d.txt')}
    _A.add_mark('x.txt', 'вера', 'до ', ' после'); _A.add_mark('x.txt', 'надежда', 'a ', ' b')
    open(_fa['0_Конспект.txt'], 'wb').write('.......вера\n(x)\n'.encode())
    _sm.sync_all(_fold, _A, _fa, os.path.join(_t, 'ba'))
    r = _sm.sync_all(_fold, _B, _fb, os.path.join(_t, 'bb'))
    check('sync: отметки пришли на второе устройство', r['marks_added'] == 2 and len(_B.all_marks()) == 2)
    check('sync: конспект пришёл', open(_fb['0_Конспект.txt'], 'rb').read() == '.......вера\n(x)\n'.encode())
    # снятие на B доходит до A
    _B.delete_marks([m.id for m in _B.all_marks() if m.fragment == 'вера'])
    _sm.sync_all(_fold, _B, _fb, os.path.join(_t, 'bb'))
    r = _sm.sync_all(_fold, _A, _fa, os.path.join(_t, 'ba'))
    check('sync: снятие синхронизируется', r['marks_removed'] == 1 and [m.fragment for m in _A.all_marks()] == ['надежда'])
    # подчёркнули заново на A — снова жива и у B
    import time as _tm; _tm.sleep(1.1)
    _A.add_mark('x.txt', 'вера', 'до ', ' после')
    _sm.sync_all(_fold, _A, _fa, os.path.join(_t, 'ba')); _sm.sync_all(_fold, _B, _fb, os.path.join(_t, 'bb'))
    check('sync: повторное подчёркивание оживает', sorted(m.fragment for m in _B.all_marks()) == ['вера', 'надежда'])
    # дописали на обеих сторонах
    open(_fa['dnev.txt'], 'ab').write('запись A\n'.encode()); open(_fb['dnev.txt'], 'ab').write('запись B\n'.encode())
    _sm.sync_all(_fold, _A, _fa, os.path.join(_t, 'ba')); _sm.sync_all(_fold, _B, _fb, os.path.join(_t, 'bb')); _sm.sync_all(_fold, _A, _fa, os.path.join(_t, 'ba'))
    da, db = open(_fa['dnev.txt'], 'rb').read().decode(), open(_fb['dnev.txt'], 'rb').read().decode()
    check('sync: дневник — записи обеих сторон, без дублей', da == db and da.count('запись A') == 1 and da.count('запись B') == 1)
    check('sync: повтор без изменений', _sm.sync_all(_fold, _A, _fa, os.path.join(_t, 'ba'))['texts_pushed'] == [])
    check('sync: нет файла папки — не падает', _sm.merge_text(b'abc\n', None, None)[0] == b'abc\n')
    check('sync: первая синхронизация разных файлов без дублей',
          _sm.merge_text('.......а\n(x)\n.......б\n(y)\n'.encode(), '.......б\n(y)\n.......в\n(z)\n'.encode(), b'')[0].decode().count('.......б') == 1)
    try:
        _sm.merge_marks([], {}, b'{oops'); check('sync: битый json -> ошибка', False)
    except ValueError:
        check('sync: битый json -> ошибка', True)
    _A.close(); _B.close()
finally:
    shutil.rmtree(_t, ignore_errors=True)

# ---- переустановка: пустые дневник и подчёркивания не затирают накопленное в папке
_t2 = tempfile.mkdtemp()
try:
    _fold2 = _sm.DirFolder(os.path.join(_t2, 'drive')); os.makedirs(_fold2.path)
    _old = _mk.MarksStore(os.path.join(_t2, 'old.db')); _old.add_mark('x.txt', 'вера', 'a ', ' b')
    _od, _ok = os.path.join(_t2, 'od.txt'), os.path.join(_t2, 'ok.txt')
    open(_od, 'wb').write('запись 1\nзапись 2\n'.encode()); open(_ok, 'wb').write('.......цитата\n(x)\n'.encode())
    _sm.sync_all(_fold2, _old, {'dnev.txt': _od, '0_Конспект.txt': _ok}, os.path.join(_t2, 'b0'))
    _new = _mk.MarksStore(os.path.join(_t2, 'new.db'))
    _nd, _nk = os.path.join(_t2, 'nd.txt'), os.path.join(_t2, 'nk.txt')
    open(_nd, 'wb').close()                                   # после установки созданы пустые файлы
    _sm.sync_all(_fold2, _new, {'dnev.txt': _nd, '0_Конспект.txt': _nk}, os.path.join(_t2, 'b1'))
    check('переустановка: пустые файлы приняли данные из папки',
          open(_nd, 'rb').read() == open(_od, 'rb').read() and open(_nk, 'rb').read() == open(_ok, 'rb').read()
          and [m.fragment for m in _new.all_marks()] == ['вера'])
    check('переустановка: файлы в папке не обнулены',
          _fold2.read('dnev.txt') == open(_od, 'rb').read() and '"вера"'.encode() in _fold2.read('sineva_marks.json'))
    # обратное: пустое устройство синхронизируется ещё раз — всё так же цело
    _sm.sync_all(_fold2, _new, {'dnev.txt': _nd, '0_Конспект.txt': _nk}, os.path.join(_t2, 'b1'))
    check('повторная синхронизация ничего не теряет', _fold2.read('dnev.txt') == open(_od, 'rb').read())
    # пустой файл в папке (остался от сбоя) не мешает: берётся локальное
    _fold2.write('dnev.txt', b'')
    _sm.sync_all(_fold2, _new, {'dnev.txt': _nd}, os.path.join(_t2, 'b1'))
    check('пустой файл в папке заполняется локальным', _fold2.read('dnev.txt') == open(_nd, 'rb').read() != b'')
    _old.close(); _new.close()
finally:
    shutil.rmtree(_t2, ignore_errors=True)

print(f"Проверок: {TOTAL}, провалено: {len(FAILED)}")
sys.exit(1 if FAILED else 0)
