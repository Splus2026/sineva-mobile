"""Цветовые темы интерфейса (день / ночь)."""
from . import textmodel

THEMES = {
    'day': dict(BG=(0.91, 0.94, 0.98, 1), CARD=(0.78, 0.85, 0.93, 1), TXT=(0.08, 0.13, 0.24, 1),
                BODY=(0.05, 0.08, 0.15, 1), ACCENT=(0.25, 0.42, 0.66, 1), GRAY=(0.45, 0.5, 0.6, 1),
                NAV=(0.84, 0.89, 0.96, 1), NAV_TXT=(0.25, 0.42, 0.66, 1), BTN2=(0.9, 0.93, 0.98, 1),
                BTN2_TXT=(0.25, 0.42, 0.66, 1), INPUT_BG=(1, 1, 1, 1), MARK_RGBA=(0.04, 0.16, 0.55, 1), MARK_COLOR='0a2a8c', HL_TEXT='',
                POP_BG=(0.17, 0.25, 0.42, 1), PANEL_BG=(0.8, 0.86, 0.94, 1), STYLE_COLOR='8a00a6'),
    'night': dict(BG=(0.12, 0.13, 0.19, 1), CARD=(0.24, 0.29, 0.48, 1), TXT=(0.93, 0.94, 0.98, 1),
                  BODY=(0.93, 0.94, 0.98, 1), ACCENT=(0.33, 0.42, 0.7, 1), GRAY=(0.4, 0.43, 0.56, 1),
                  NAV=(0.09, 0.1, 0.15, 1), NAV_TXT=(0.75, 0.8, 0.96, 1), BTN2=(0.19, 0.22, 0.36, 1),
                  BTN2_TXT=(0.8, 0.85, 1, 1), INPUT_BG=(0.17, 0.2, 0.32, 1), MARK_RGBA=(0.62, 0.78, 1, 1), MARK_COLOR='9ec7ff', HL_TEXT='000000',
                  POP_BG=(0.21, 0.17, 0.35, 1), PANEL_BG=(0.17, 0.19, 0.31, 1), STYLE_COLOR='ff7aeb'),
}


_values = {}                 # текущие цвета темы
_users = []                  # globals() модулей интерфейса, где цвета лежат обычными именами (ACCENT, TXT, …)


def register(g):
    """Модуль интерфейса подписывается на цвета: apply_theme обновляет его глобальные имена."""
    _users.append(g)
    g.update(_values)


def apply_theme(name):
    """Переключает цвета во всех подписанных модулях; интерфейс после этого нужно пересоздать."""
    t = THEMES.get(name, THEMES['day'])
    _values.update({k: v for k, v in t.items() if k not in ('MARK_COLOR', 'HL_TEXT', 'STYLE_COLOR')})
    for g in _users:
        g.update(_values)
    textmodel.MARK_COLOR = t['MARK_COLOR']
    textmodel.HL_TEXT = t['HL_TEXT']
    textmodel.STYLE_COLOR = t['STYLE_COLOR']


apply_theme('day')
YELLOW = (1, 1, 0, 1)                    # слово запроса: #FFFF00 (как на ПК)
YELLOW_SOFT = (1, 1, 0.6, 1)             # часть слова: #FFFF99
SEL_BLUE = (0.67, 0.8, 1, 1)             # выделение пальцем: #AACCFF
