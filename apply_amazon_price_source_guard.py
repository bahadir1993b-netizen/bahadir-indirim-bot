from pathlib import Path

P = Path('marketplace_scanner.py')
s = P.read_text(encoding='utf-8')

# Amazon search kartlarında min/max fiyat almak hatalıdır: kartta kupon,
# taksit veya başka bir fiyat sinyali bulunabilir. Güncel fiyatı yalnızca
# strike olmayan ana .a-price alanından, önceki fiyatı strike alandan al.
old = '''            vals = []\n            for sel in ['.a-price .a-offscreen', '.a-text-price .a-offscreen', '[data-a-strike="true"] .a-offscreen']:\n                for el in card.select(sel):\n                    x = money(el.get_text(' ', strip=True))\n                    if x and x not in vals:\n                        vals.append(x)\n            if len(vals) < 2:\n                vals = _raw_card_prices(card.get_text(' ', strip=True))\n            if len(vals) < 2:\n                continue\n            current, previous = min(vals), max(vals)\n'''
new = '''            current_vals = []\n            previous_vals = []\n            for el in card.select('.a-price:not(.a-text-price) .a-offscreen'):\n                x = money(el.get_text(' ', strip=True))\n                if x and x not in current_vals:\n                    current_vals.append(x)\n            for el in card.select('.a-text-price .a-offscreen, [data-a-strike="true"] .a-offscreen'):\n                x = money(el.get_text(' ', strip=True))\n                if x and x not in previous_vals:\n                    previous_vals.append(x)\n            if not current_vals or not previous_vals:\n                continue\n            current = current_vals[0]\n            previous = min((x for x in previous_vals if x > current), default=None)\n            if previous is None:\n                continue\n'''
if old not in s:
    raise SystemExit('Amazon fiyat bloğu bulunamadı')
s = s.replace(old, new, 1)
compile(s, str(P), 'exec')
P.write_text(s, encoding='utf-8')
print('AMAZON PRICE SOURCE GUARD OK | current=non-strike main price | previous=strike price')
