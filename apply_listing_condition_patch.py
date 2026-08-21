from pathlib import Path
import re

p = Path('telegram_sources.py')
s = p.read_text(encoding='utf-8')

if 'KAMPANYA PATCH V3' in s:
    print('Listing detail + campaign patch V3 already active')
    raise SystemExit(0)

# Runtime patches change the send() body, so never depend on a fragile
# coupon-specific source line. Inject immediately before the final CTA.
anchor_re = re.compile(r"(?m)^(\s*)lines\+=\[\s*['\"]['\"]\s*,\s*['\"]👇 Fırsata git['\"]\s*\]")
m = anchor_re.search(s)
if not m:
    # Fallback: locate the CTA anywhere inside send().
    send_start = s.find('def send(')
    if send_start >= 0:
        tail = s[send_start:]
        mm = re.search(r"(?m)^(\s*)lines.*Fırsata git.*$", tail)
        if mm:
            m = type('M', (), {'start': lambda self: send_start + mm.start(), 'end': lambda self: send_start + mm.end(), 'group': lambda self, n: mm.group(n)})()

if not m:
    raise SystemExit('Telegram CTA insertion noktası bulunamadı')

indent = m.group(1)
block = f'''{indent}# KAMPANYA PATCH V3: kaynak mesajındaki durum/satıcı/stok/yorum/kampanyayı koru.
{indent}sig = signal or ''
{indent}detail_patterns = [
{indent}    (r'(?:📦\\s*)?(?:durum|ürün durumu)\\s*[:：-]\\s*([^\\n|]+)', '📦 Durum'),
{indent}    (r'(?:📦\\s*)?(?:ikinci el|2\\.?\\s*el)\\s*[:：-]?\\s*([^\\n|]+)?', '📦 Durum'),
{indent}    (r'(?:💬\\s*)?yorum\\s*[:：-]\\s*([^\\n|]+)', '💬 Yorum'),
{indent}    (r'(?:🏪\\s*)?(?:satıcı|satici)\\s*[:：-]\\s*([^\\n|]+)', '🏪 Satıcı'),
{indent}    (r'(?:📦\\s*)?stok\\s*[:：-]\\s*([^\\n|]+)', '📦 Stok'),
{indent}]
{indent}seen_detail = set()
{indent}for pat, label in detail_patterns:
{indent}    dm = re.search(pat, sig, re.I)
{indent}    if not dm:
{indent}        continue
{indent}    value = (dm.group(1) or '').strip(' -*_')
{indent}    if label == '📦 Durum' and not value:
{indent}        value = 'İkinci El'
{indent}    if value and value.lower() not in seen_detail:
{indent}        lines.append(f'{{label}}: {{value[:180]}}')
{indent}        seen_detail.add(value.lower())

{indent}campaign_patterns = [
{indent}    r'(?:🎯\\s*)?(?:KAMPANYA|KAMPANYALAR|KAMPANYASI)\\s*[:：-]\\s*([^\\n]+)',
{indent}    r'([^\\n.!?]{{0,160}}\\b(?:\\d+\\s*adet|2\\s*adet|3\\s*adet|4\\s*adet)\\b[^\\n.!?]{{0,220}}\\b(?:%\\s*\\d+|indirim|avantaj|kazanın|kazan[ıi]n|ödeme ekranında)\\b[^\\n.!?]*)',
{indent}    r'([^\\n.!?]{{0,160}}\\b(?:sepete|satın alın|sepete ekleyin)\\b[^\\n.!?]{{0,220}}\\b(?:indirim|kampanya|ödeme ekranında)\\b[^\\n.!?]*)',
{indent}]
{indent}campaign = None
{indent}for pat in campaign_patterns:
{indent}    cm = re.search(pat, sig, re.I)
{indent}    if cm:
{indent}        candidate = re.sub(r'\\s+', ' ', cm.group(1)).strip(' -*_')
{indent}        if len(candidate) >= 8:
{indent}            campaign = candidate[:240]
{indent}            break
{indent}if campaign and not any('Kampanya:' in x for x in lines):
{indent}    lines.append(f'🎯 Kampanya: {{campaign}}')

'''
s = s[:m.start()] + block + s[m.start():]
if 'KAMPANYA PATCH V3' not in s:
    raise SystemExit('patch eklenemedi')
compile(s, str(p), 'exec')
p.write_text(s, encoding='utf-8')
print('Listing detail + campaign patch V3 OK')
