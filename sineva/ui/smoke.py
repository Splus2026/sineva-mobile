"""Заставка: слово «Синева Mobile» собирается из пикселей, как из клубов дыма. Чистая математика без Kivy
   (тестируется отдельно); рисует её sineva.ui.splash."""
import math
import random

ROWS = 7
_G = {
    'С': ['.###.', '#...#', '#....', '#....', '#....', '#...#', '.###.'],
    'и': ['.....', '.....', '#...#', '#..##', '#.#.#', '##..#', '#...#'],
    'н': ['.....', '.....', '#...#', '#...#', '#####', '#...#', '#...#'],
    'е': ['.....', '.....', '.###.', '#...#', '#####', '#....', '.####'],
    'в': ['.....', '.....', '####.', '#...#', '####.', '#...#', '####.'],
    'а': ['.....', '.....', '.###.', '....#', '.####', '#...#', '.####'],
    'M': ['#...#', '##.##', '#.#.#', '#...#', '#...#', '#...#', '#...#'],
    'o': ['.....', '.....', '.###.', '#...#', '#...#', '#...#', '.###.'],
    'b': ['#....', '#....', '####.', '#...#', '#...#', '#...#', '####.'],
    'i': ['..#..', '.....', '.##..', '..#..', '..#..', '..#..', '.###.'],
    'l': ['.##..', '..#..', '..#..', '..#..', '..#..', '..#..', '.###.'],
    ' ': ['...', '...', '...', '...', '...', '...', '...'],
}
_G['E'] = _G['е']                       # на случай латинской/кириллической «e» в строке
_G['e'] = _G['е']


def layout(text):
    """[(колонка, строка)] светящихся клеток, число колонок. Клетка — один «пиксель» шрифта 5×7."""
    cells, col = [], 0
    for ch in text:
        g = _G.get(ch, _G[' '])
        for r, row in enumerate(g):
            for c, v in enumerate(row):
                if v == '#':
                    cells.append((col + c, r))
        col += len(g[0]) + 1
    return cells, col - 1


class SmokeText:
    DUR = 2.6

    def __init__(self, text='Синева Mobile', sub=2, seed=7):
        cells, self.cols = layout(text)
        self.rows, self.sub = ROWS, sub
        rnd = random.Random(seed)
        self.p = []
        for c, r in cells:
            for a in range(sub):
                for b in range(sub):
                    tx, ty = c + (a + .5) / sub, r + (b + .5) / sub
                    delay = (tx / self.cols) * self.DUR * 0.4 + rnd.random() * 0.35
                    sx = tx + rnd.gauss(0, 1) * self.cols * 0.30
                    sy = ty + ROWS * 1.5 + abs(rnd.gauss(0, 1)) * ROWS * 1.4      # дым поднимается снизу
                    self.p.append((tx, ty, sx, sy, delay, rnd.random() * 6.283))
        self.assembled_at = max(p[4] for p in self.p) + self.DUR * 0.5

    def __len__(self):
        return len(self.p)

    def state(self, i, t):
        """(x, y, масштаб, прозрачность) частицы i в момент t; координаты в клетках, y вниз."""
        tx, ty, sx, sy, delay, ph = self.p[i]
        u = min(1.0, max(0.0, (t - delay) / (self.DUR * 0.5)))
        e = u * u * (3 - 2 * u)
        amp = (1 - e) * 2.2
        x = sx + (tx - sx) * e + amp * math.sin(1.7 * t + ph)
        y = sy + (ty - sy) * e + amp * math.cos(1.3 * t + ph)
        return x, y, 1.9 - 0.9 * e, min(1.0, u * 1.8)
