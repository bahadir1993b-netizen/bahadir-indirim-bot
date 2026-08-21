from pathlib import Path

p = Path('telegram_sources.py')
s = p.read_text(encoding='utf-8')

if 'KAMPANYA PATCH V2' in s:
    print('Listing detail + campaign patch already active')
    raise SystemExit(0)

anchor = "    if coupon:lines.append(f'🎟️ Kupon: {coupon}')"
if anchor not in s:
    raise SystemExit('coupon insertion noktası bulunamadı')

block = r'''    # KAMPANYA PATCH V2: kaynak Telegram mesajındaki durum, satıcı, stok,
    # yorum ve kampanya bilgisini ürün mesajına taşı.
    sig = signal or ''
    detail_patterns = [
        (r'(?:📦\s*)?(?:durum|ürün durumu)\s*[:：-]\s*([^\n|]+)', '📦 Durum'),
        (r'(?:📦\s*)?(?:ikinci el|2\.?\s*el)\s*[:：-]?\s*([^\n|]+)?', '📦 Durum'),
        (r'(?:💬\s*)?yorum\s*[:：-]\s*([^\n|]+)', '💬 Yorum'),
        (r'(?:🏪\s*)?(?:satıcı|satici)\s*[:：-]\s*([^\n|]+)', '🏪 Satıcı'),
        (r'(?:📦\s*)?stok\s*[:：-]\s*([^\n|]+)', '📦 Stok'),
    ]
    seen_detail = set()
    for pat, label in detail_patterns:
        m = re.search(pat, sig, re.I)
        if not m:
            continue
        value = (m.group(1) or '').strip(' -*_')
        if label == '📦 Durum' and not value:
            value = 'İkinci El'
        if value and value.lower() not in seen_detail:
            lines.append(f'{label}: {value[:180]}')
            seen_detail.add(value.lower())

    campaign_patterns = [
        r'(?:🎯\s*)?(?:KAMPANYA|KAMPANYASI)\s*[:：-]\s*([^\n]+)',
        r'([^\n.!?]{0,120}\b(?:\d+\s*adet|2\s*adet|3\s*adet|4\s*adet)\b[^\n.!?]{0,180}\b(?:%\s*\d+|indirim|avantaj|kazanın|kazan[ıi]n|ödeme ekranında)\b[^\n.!?]*)',
        r'([^\n.!?]{0,120}\b(?:sepete|satın alın|sepete ekleyin)\b[^\n.!?]{0,180}\b(?:indirim|kampanya|ödeme ekranında)\b[^\n.!?]*)',
    ]
    campaign = None
    for pat in campaign_patterns:
        m = re.search(pat, sig, re.I)
        if m:
            candidate = re.sub(r'\s+', ' ', m.group(1)).strip(' -*_')
            if len(candidate) >= 8:
                campaign = candidate[:240]
                break
    if campaign:
        lines.append(f'🎯 Kampanya: {campaign}')
'''

s = s.replace(anchor, block + anchor, 1)
if 'KAMPANYA PATCH V2' not in s:
    raise SystemExit('patch eklenemedi')
compile(s, str(p), 'exec')
p.write_text(s, encoding='utf-8')
print('Listing detail + campaign patch V2 OK')
