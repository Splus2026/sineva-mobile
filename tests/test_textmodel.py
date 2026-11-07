import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from sineva.ui.textmodel import build_markup, atom_at, sentence_bounds

ok = True
def check(n, c, extra=""):
    global ok
    if not c: ok = False; print("FAIL:", n, extra)

t = "Вера и любовь. Верность ценнее [золота] & денег! Конец."
m, refs = build_markup(t, [(0, 4, 2), (15, 19, 1)], [(5, 6)])
check('ссылки только у стилизованных', m.count('[ref=') == 3 and len(refs) == 3)
check('подчёркнутое тёмно-синим', '[color=0a2a8c]' in m)
check('экранирование', '&bl;золота&br;' in m and '&amp;' in m)
check('уровни подсветки', sorted(r['hl'] for r in refs.values()) == [0, 1, 2])
plain = m
import re
back = re.sub(r'\[/?(ref=\d+|ref|color=[0-9a-f]+|color)\]', '', m).replace('&bl;', '[').replace('&br;', ']').replace('&amp;', '&')
check('текст не искажён', back == t)
# режим выделения: каждое слово — свой атом, текст цел
m2, refs2 = build_markup(t, [(0, 4, 2)], [], sel=(5, 14), select_mode=True)
back2 = re.sub(r'\[/?(ref=\d+|ref|color=[0-9a-f]+|color)\]', '', m2).replace('&bl;', '[').replace('&br;', ']').replace('&amp;', '&')
check('режим выделения: текст цел', back2 == t)
check('слов-атомов не меньше слов', sum(1 for r in refs2.values()) >= len(t.split()))
check('выделение покрывает атомы', all(r['sel'] for r in refs2.values() if 5 <= r['start'] and r['end'] <= 14 and t[r['start']:r['end']].strip()))
# попадание пальцем
zones = {str(i): [(10 * i, 0, 10 * i + 9, 20)] for i in refs2}
a = atom_at(refs2, zones, 12, 5)
check('атом под пальцем найден', a is not None and a[1] > a[0])
check('вне текста — None', atom_at(refs2, zones, 9999, 5) is None)
# предложения
s, e = sentence_bounds(t, 20, 24)
check('предложение', t[s:e] == "Верность ценнее [золота] & денег!", repr(t[s:e]))
s, e = sentence_bounds(t, 0, 4)
check('первое предложение', t[s:e] == "Вера и любовь.")
s, e = sentence_bounds(t, t.index('Конец'), t.index('Конец') + 3)
check('последнее предложение', t[s:e] == "Конец.", repr(t[s:e]))
print('textmodel OK' if ok else 'textmodel FAILED')
sys.exit(0 if ok else 1)
