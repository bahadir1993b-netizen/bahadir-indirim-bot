from pathlib import Path
import re

p = Path('telegram_sources.py')
s = p.read_text(encoding='utf-8')

old = "    if coupon:lines.append(f'🎟️ Kupon: {coupon}')\n    lines+=['','👇 Fırsata git']"
new = r'''    if coupon:lines.append(f'🎟️ Kupon: {coupon}')
    # Kaynak mesajındaki kampanya metnini aynen/özet olarak taşı.
    # Özellikle "2 adet satın alın, 1 adette %40 indirim" gibi fiyat olmayan
    # ama gerçek kampanya bilgisini yalnızca kupon olarak değerlendirmiyoruz.
    campaign_patterns = [
        r'(?:🎯\s*)?(?:KAMPANYA|KAMPANYASI)\s*[:：-]\s*([^\n]+)',
        r'([^\n.!?]{0,100}\b(?:\d+\s*adet|2\s*adet|3\s*adet)\b[^\n.!?]{0,180}\b(?:%\s*\d+|indirim|avantaj|kazanın|kazan[ıi]n|ödeme ekranında)\b[^\n.!?]*)',
        r'([^\n.!?]{0,120}\b(?:sepete|sepete ekleyin|satın alın)\b[^\n.!?]{0,180}\b(?:indirim|kampanya|ödeme ekranında)\b[^\n.!?]*)',
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
    lines+=['','👇 Fırsata git']'''
if old not in s:
    raise SystemExit('campaign insertion noktası bulunamadı')
s=s.replace(old,new,1)
p.write_text(s,encoding='utf-8')
compile(s,str(p),'exec')
print('Telegram campaign extraction patch OK')
