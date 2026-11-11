"""Родная отрисовка карточек (Android WebView): чистый Python без Kivy, поэтому проверяется тестами.

Карточка = HTML с «атомами»-словами <span class="w" data-a data-b>: подсветка найденного (hl1/hl2), подчёркнутое (mk),
выделение двумя касаниями (первое слово → последнее слово) обрабатывает JS и присылает событие {'t':'select',key,s,e}.
События забирает Python опросом window.SV.take(); команды в страницу — через evaluateJavascript."""
import html
import json
import re
from typing import Dict, List

WORD_RE = re.compile(r'\S+\s*')
_TAG = re.compile(r'\[(/?)(b|i|size|color)(?:=([^\]]*))?\]')


def _plain(text: str) -> str:
    """Текст из разметки Kivy (экранирование &bl; &br; &amp;) -> обычный текст."""
    return text.replace('&bl;', '[').replace('&br;', ']').replace('&amp;', '&')


def kivy_to_html(markup: str) -> str:
    """Заголовок карточки: [b] [i] [size=12sp] [color=777777] -> HTML."""
    out, pos, stack = [], 0, []
    for m in _TAG.finditer(markup):
        out.append(html.escape(_plain(markup[pos:m.start()])))
        pos = m.end()
        closing, tag, arg = m.group(1), m.group(2), m.group(3)
        if closing:
            if stack and stack[-1][0] == tag:
                out.append(stack.pop()[1])
            continue
        if tag in ('b', 'i'):
            out.append(f'<{tag}>')
            stack.append((tag, f'</{tag}>'))
        elif tag == 'size':
            px = re.sub(r'[^0-9.]', '', arg or '') or '12'
            out.append(f'<span style="font-size:{px}px">')
            stack.append((tag, '</span>'))
        else:
            col = re.sub(r'[^0-9a-fA-F]', '', arg or '')
            out.append(f'<span style="color:#{col}">')
            stack.append((tag, '</span>'))
    out.append(html.escape(_plain(markup[pos:])))
    while stack:
        out.append(stack.pop()[1])
    return ''.join(out)


def atoms(text: str, hl, marks, extra=()):
    """[(a, b, prio, mark)] — отрезки текста с одним стилем; слово и его пробел раздельно."""
    n = len(text)
    cuts = {0, n}
    for s, e, _p in hl:
        cuts.update((s, e))
    for s, e in marks:
        cuts.update((s, e))
    cuts.update(extra)
    for m in WORD_RE.finditer(text):
        cuts.add(m.start())
        cuts.add(m.start() + len(m.group().rstrip()))
        cuts.add(m.end())
    cuts = sorted(c for c in cuts if 0 <= c <= n)
    res = []
    for a, b in zip(cuts, cuts[1:]):
        if a == b:
            continue
        prio = max([p for s, e, p in hl if s <= a and b <= e], default=0)
        mark = any(s <= a and b <= e for s, e in marks)
        res.append((a, b, prio, mark))
    return res


def _cov(spans, a, b):
    return any(s <= a and b <= e for s, e in spans)


def _note_html(item) -> str:
    _pos, text, flags, nid = item
    cls = ['nt']
    if 'u' in flags:
        cls.append('mk')
    if 'b' in flags:
        cls.append('bg')
    if 'c' in flags:
        cls.append('cl')
    return f'<span class="{" ".join(cls)}" data-nid="{int(nid)}"> ({html.escape(text)})</span>'


def card_html(card: dict, with_expand: bool = False) -> str:
    """HTML одной карточки. Координаты data-a/data-b — позиции в card['text'] (как у Kivy-версии).
       Размер («+Шрифт») и цвет («+Цвет») действуют только внутри подчёркнутого; свои мысли — курсивом в скобках."""
    text = card['text']
    big, color, notes = card.get('big', ()), card.get('color', ()), card.get('notes', ())
    extra = [x for sp_ in (big, color) for se in sp_ for x in se] + [n[0] for n in notes]
    at = {}
    for n in notes:
        at.setdefault(min(max(n[0], 0), len(text)), []).append(n)
    parts = [_note_html(n) for n in at.get(0, ())]
    for a, b, prio, mark in atoms(text, card['hl'], card['marks'], extra):
        seg = text[a:b]
        cls = ['w'] if seg.strip() else ['s']
        if prio:
            cls.append(f'hl{min(prio, 2)}')
        if mark:
            cls.append('mk')
            if _cov(big, a, b):
                cls.append('bg')
            if _cov(color, a, b):
                cls.append('cl')
        parts.append(f'<span class="{" ".join(cls)}" data-a="{a}" data-b="{b}">{html.escape(seg)}</span>')
        parts.extend(_note_html(n) for n in at.get(b, ()))
    key = html.escape(card['key'], quote=True)
    btn = ''
    if with_expand:
        btn = (f'<button class="ex" data-key="{key}">{"Свернуть" if card.get("expanded") else "Полностью"}</button>')
    return (f'<div class="card" data-key="{key}"><div class="hd">{kivy_to_html(card["header"])}</div>'
            f'<div class="tx">{"".join(parts)}</div>{btn}</div>')


def page_html(theme: Dict, font_px: int, step: int = 2) -> str:
    """Каркас страницы: стили темы и JS. Карточки подставляются командами setCards/appendCards/updateCard."""
    def css(rgba):
        return 'rgb(%d,%d,%d)' % tuple(int(round(c * 255)) for c in rgba[:3])
    body = css(theme['BODY'])
    return PAGE.replace('__BG__', css(theme['BG'])).replace('__CARD__', css(theme['CARD'])) \
        .replace('__TXT__', body).replace('__MARK__', '#' + theme['MARK_COLOR']) \
        .replace('__HLTXT__', '#' + (theme['HL_TEXT'] or '000000')).replace('__ACCENT__', css(theme['ACCENT'])) \
        .replace('__STYLE__', '#' + theme.get('STYLE_COLOR', '8a00a6')).replace('__STEP__', str(int(step))).replace('__FONT__', str(int(font_px)))


PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<style>
html,body{margin:0;padding:0;background:__BG__;-webkit-tap-highlight-color:transparent}
body{padding:6px;font-family:sans-serif}
.card{background:__CARD__;border-radius:10px;padding:10px 12px;margin:0 0 8px 0;color:__TXT__}
.hd{font-size:14px;margin-bottom:4px}
.tx{font-size:__FONT__px;line-height:1.35;white-space:pre-wrap;word-wrap:break-word;-webkit-user-select:none;user-select:none}
.hl1{background:#FFFF99;color:__HLTXT__}.hl2{background:#FFFF00;color:__HLTXT__}
.mk{color:__MARK__;box-shadow:inset 0 -3px 0 currentColor}   /* сплошная жирная линия, не рвётся на Ц Щ р Д */
.hl1.mk,.hl2.mk{color:#0a2a8c}
.sel{background:#9ec7ff;color:#000}
.nt{font-style:italic}
.cl{color:__STYLE__}
.hl1.cl,.hl2.cl{color:#5a0070}
.bg{font-size:calc(1em + __STEP__px)}
button{display:block;width:100%;box-sizing:border-box;margin:6px 0;padding:12px;border:0;border-radius:8px;
 background:__ACCENT__;color:#fff;font-size:16px}
button.ex{padding:6px;font-size:14px;margin-top:6px;background:transparent;color:__ACCENT__;border:1px solid __ACCENT__}
#more{display:none}
</style><style id="fs"></style></head><body><div id="cards"></div><button id="more">Показать ещё</button>
<script>
(function(){
var ev=[], anchor=null, ro=false;
function clearAnchor(){ if(anchor){ anchor.el.classList.remove('sel'); anchor=null; } }
document.addEventListener('click',function(e){
  if(Date.now()-swiped<500) return;
  var t=e.target;
  if(t.id==='more'){ ev.push({t:'more'}); return; }
  if(t.classList.contains('ex')){ ev.push({t:'expand',key:t.getAttribute('data-key')}); return; }
  if(t.classList.contains('nt')){ if(ro){ return; } ev.push({t:'note',nid:t.getAttribute('data-nid')}); return; }
  if(ro){ return; }
  if(!t.classList.contains('w')){ clearAnchor(); return; }
  var card=t.closest('.card'); var key=card.getAttribute('data-key');
  var a=+t.getAttribute('data-a'), b=+t.getAttribute('data-b');
  if(!anchor || anchor.key!==key){ clearAnchor(); t.classList.add('sel'); anchor={key:key,a:a,b:b,el:t}; ev.push({t:'anchor',key:key,s:a,e:b}); return; }
  var s=Math.min(anchor.a,a), en=Math.max(anchor.b,b);
  clearAnchor();
  ev.push({t:'select',key:key,s:s,e:en});
});
// листание свайпом: влево — следующий блок, вправо — предыдущий
var sx=0, sy=0, swiped=0;
document.addEventListener('touchstart',function(e){ var t=e.touches[0]; sx=t.clientX; sy=t.clientY; },{passive:true});
document.addEventListener('touchend',function(e){
  if(e.touches.length) return;
  var t=e.changedTouches[0], dx=t.clientX-sx, dy=t.clientY-sy;
  if(Math.abs(dx)>90 && Math.abs(dy)<60 && Math.abs(dx)>2*Math.abs(dy)){ swiped=Date.now(); ev.push({t:'swipe',d:dx<0?1:-1}); }
},{passive:true});
function cards(){ return document.getElementById('cards'); }
window.SV={
  ready:true,
  setCards:function(h){ clearAnchor(); cards().innerHTML=h; window.scrollTo(0,0); },
  appendCards:function(h,drop){
    clearAnchor(); var c=cards(), dh=0;
    for(var i=0;i<drop && c.firstElementChild;i++){ var el=c.firstElementChild; dh+=el.offsetHeight+8; c.removeChild(el); }
    c.insertAdjacentHTML('beforeend',h);
    if(dh) window.scrollBy(0,-dh);
  },
  updateCard:function(key,h){
    clearAnchor(); var els=cards().children;
    for(var i=0;i<els.length;i++){ if(els[i].getAttribute('data-key')===key){ els[i].outerHTML=h; return; } }
  },
  setMore:function(label){ var m=document.getElementById('more'); if(label){ m.textContent=label; m.style.display='block'; } else m.style.display='none'; },
  setFont:function(px,step){ document.getElementById('fs').textContent='.tx{font-size:'+px+'px}.bg{font-size:calc(1em + '+(step||2)+'px)}'; },
  setReadonly:function(v){ ro=!!v; },
  scrollTop:function(){ window.scrollTo(0,0); },
  err:function(m){ ev.push({t:'err',m:String(m)}); },
  info:function(){ var c=cards().firstElementChild; return c?(c.getAttribute('data-key')+' · '+c.textContent.length+' зн.'):'пусто'; },
  take:function(){ var r=JSON.stringify(ev); ev=[]; return r; }
};
})();
</script></body></html>"""


def js_call(fn: str, *args) -> str:
    """Строка JS: SV.fn(args...), аргументы — JSON-литералы (кавычки и переводы строк экранированы)."""
    return (f"try{{window.SV&&window.SV.{fn}({','.join(json.dumps(a, ensure_ascii=False) for a in args)});}}"
            f"catch(e){{window.SV&&window.SV.err('{fn}: '+e);}}")
