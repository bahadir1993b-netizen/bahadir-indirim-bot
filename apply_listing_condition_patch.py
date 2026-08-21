from pathlib import Path

p = Path('telegram_sources.py')
s = p.read_text(encoding='utf-8')

needle = "    if p and p>c:lines.append(f'🏷️ Önceki: {p:,.2f} TL'.replace(',','X').replace('.',',').replace('X','.'))\n    if coupon:lines.append(f'🎟️ Kupon: {coupon}')"
replacement = """    if p and p>c:lines.append(f'🏷️ Önceki: {p:,.2f} TL'.replace(',','X').replace('.',',').replace('X','.'))
    # Kaynak paylaşımında ürünün kondisyonu, satıcısı, stok ve açıklaması
    # veriliyorsa bunları da aktar. Böylece 'İkinci El: Yeni Gibi' ürünler
    # sıfır ürün gibi görünmez.
    detail_patterns = [
        (r'(?:📦\\s*)?(?:durum|ürün durumu)\\s*[:：-]\\s*([^\\n|]+)', '📦 Durum'),
        (r'(?:📦\\s*)?(?:ikinci el|2\\.?\\s*el)\\s*[:：-]?\\s*([^\\n|]+)?', '📦 Durum'),
        (r'(?:💬\\s*)?yorum\\s*[:：-]\\s*([^\\n|]+)', '💬 Yorum'),
        (r'(?:🏪\\s*)?(?:satıcı|satici)\\s*[:：-]\\s*([^\\n|]+)', '🏪 Satıcı'),
        (r'(?:📦\\s*)?stok\\s*[:：-]\\s*([^\\n|]+)', '📦 Stok'),
    ]
    seen_details = set()
    sig = signal or ''
    for pat, label in detail_patterns:
        m = re.search(pat, sig, re.I)
        if not m:
            continue
        value = (m.group(1) or '').strip(' -*_')
        if label == '📦 Durum' and not value:
            value = 'İkinci El'
        if value and value.lower() not in seen_details:
            lines.append(f'{label}: {value[:180]}')
            seen_details.add(value.lower())
    if coupon:lines.append(f'🎟️ Kupon: {coupon}')"""
if needle not in s:
    raise SystemExit('telegram_sources.py detail insertion noktası bulunamadı')
s = s.replace(needle, replacement, 1)
p.write_text(s, encoding='utf-8')
compile(s, str(p), 'exec')
print('Listing condition/detail patch OK')
