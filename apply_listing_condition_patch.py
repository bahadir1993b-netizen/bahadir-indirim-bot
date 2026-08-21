from pathlib import Path
import re

p = Path('telegram_sources.py')
s = p.read_text(encoding='utf-8')
needle = "    if p and p>c:lines.append(f'🏷️ Önceki: {p:,.2f} TL'.replace(',','X').replace('.',',').replace('X','.'))\n    if coupon:lines.append(f'🎟️ Kupon: {coupon}')"
replacement = r'''    if p and p>c:lines.append(f'🏷️ Önceki: {p:,.2f} TL'.replace(',','X').replace('.',',').replace('X','.'))
    # Kaynak mesajındaki kondisyon/satıcı/stok/yorum bilgilerini koru.
    detail_patterns = [
        (r'(?:📦\s*)?(?:durum|ürün durumu)\s*[:：-]\s*([^\n|]+)', '📦 Durum'),
        (r'(?:📦\s*)?(?:ikinci el|2\.?\s*el)\s*[:：-]?\s*([^\n|]+)?', '📦 Durum'),
        (r'(?:💬\s*)?yorum\s*[:：-]\s*([^\n|]+)', '💬 Yorum'),
        (r'(?:🏪\s*)?(?:satıcı|satici)\s*[:：-]\s*([^\n|]+)', '🏪 Satıcı'),
        (r'(?:📦\s*)?stok\s*[:：-]\s*([^\n|]+)', '📦 Stok'),
    ]
    sig = signal or ''
    seen_details=set()
    for pat,label in detail_patterns:
        m=re.search(pat,sig,re.I)
        if not m: continue
        value=(m.group(1) or '').strip(' -*_')
        if label=='📦 Durum' and not value: value='İkinci El'
        if value and value.lower() not in seen_details:
            lines.append(f'{label}: {value[:180]}'); seen_details.add(value.lower())
    if coupon:lines.append(f'🎟️ Kupon: {coupon}')
    # Kampanyayı yalnızca kupon kodu sanma. Kaynak mesajındaki "2 adet satın alın,
    # 1 adette %40 indirim" ve "sepete 2 adet ekleyin" gibi metinleri de taşı.
    campaign_patterns=[
        r'(?:🎯\s*)?(?:KAMPANYA|KAMPANYASI)\s*[:：-]\s*([^\n]+)',
        r'([^\n.!?]{0,120}\b(?:\d+\s*adet|2\s*adet|3\s*adet)\b[^\n.!?]{0,180}\b(?:%\s*\d+|indirim|avantaj|kazanın|kazan[ıi]n|ödeme ekranında)\b[^\n.!?]*)',
        r'([^\n.!?]{0,120}\b(?:sepete|satın alın)\b[^\n.!?]{0,180}\b(?:indirim|kampanya|ödeme ekranında)\b[^\n.!?]*)',
    ]
    campaign=None
    for pat in campaign_patterns:
        m=re.search(pat,sig,re.I)
        if m:
            candidate=re.sub(r'\s+',' ',m.group(1)).strip(' -*_')
            if len(candidate)>=8: campaign=candidate[:240]; break
    if campaign: lines.append(f'🎯 Kampanya: {campaign}')'''
if needle not in s:
    raise SystemExit('detail/campaign insertion noktası bulunamadı')
s=s.replace(needle,replacement,1)
p.write_text(s,encoding='utf-8')
compile(s,str(p),'exec')
print('Listing detail + campaign patch OK')
