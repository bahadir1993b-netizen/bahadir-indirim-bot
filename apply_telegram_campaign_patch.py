from pathlib import Path

p = Path('telegram_sources.py')
s = p.read_text(encoding='utf-8')

marker = 'def send(s,u,t,c,p,source,post_id,signal,coupon=None):'
if marker not in s:
    raise SystemExit('send fonksiyonu bulunamadı')

helper = r'''
def extract_campaign(text):
    """Kaynak mesajındaki Amazon/marketplace kampanya cümlelerini yakalar."""
    if not text:
        return None
    clean = re.sub(r'\s+', ' ', htmlmod.unescape(text)).strip()
    patterns = [
        r'(?:🎯\s*)?(?:KAMPANYA|KAMPANYALAR?)\s*[:：-]?\s*(.{10,300}?)(?=\s*(?:https?://|$))',
        r'(\d+\s*(?:adet|ürün)\s+satın\s+al(?:ın)?[^.!\n]{0,220}%\s*\d{1,2}\s*indirim[^.!\n]*)',
        r'(\d+\s*(?:adet|ürün)\s+ekleyin[^.!\n]{0,220}(?:indirim|kampanya)[^.!\n]*)',
        r'((?:sepette|ödeme ekranında)[^.!\n]{0,220}(?:indirim|kampanya)[^.!\n]*)',
    ]
    for pat in patterns:
        m = re.search(pat, clean, re.I)
        if m:
            value = m.group(1).strip(' -:')
            if len(value) >= 10:
                return value[:280]
    return None


def page_campaign(u):
    """Ürün sayfasında kaynak mesajında olmayan kampanya bilgisini ara."""
    try:
        r = requests.get(u, headers=HEAD, timeout=8)
        if r.status_code >= 400:
            return None
        soup = BeautifulSoup(r.text, 'html.parser')
        text = soup.get_text(' ', strip=True)
        patterns = [
            r'(?:Kampanyalar?|KAMPANYALAR?)\s*[:：-]?\s*(\d+\s*(?:adet|ürün)\s+satın\s+al[^.]{0,300}?%\s*\d{1,2}\s*indirim[^.]{0,120})',
            r'(\d+\s*(?:adet|ürün)\s+satın\s+al[^.]{0,300}?%\s*\d{1,2}\s*indirim[^.]{0,120})',
            r'(\d+\s*(?:adet|ürün)\s+ekleyin[^.]{0,250}(?:indirim|kampanya)[^.]{0,120})',
        ]
        for pat in patterns:
            m = re.search(pat, text, re.I)
            if m:
                value = re.sub(r'\s+', ' ', m.group(1)).strip(' -:')
                if len(value) >= 10:
                    return value[:280]
    except Exception as e:
        print(f'KAMPANYA SAYFA OKUMA HATASI | {type(e).__name__}: {e}')
    return None

'''
if 'def extract_campaign(' not in s:
    s = s.replace(marker, helper + marker, 1)

old = "    if coupon:lines.append(f'🎟️ Kupon: {coupon}')\n    lines+=['','👇 Fırsata git']"
new = "    if coupon:lines.append(f'🎟️ Kupon: {coupon}')\n    campaign = extract_campaign(signal) or page_campaign(u)\n    if campaign:\n        lines.append(f'🎯 Kampanya: {campaign}')\n    lines+=['','👇 Fırsata git']"
if old not in s:
    raise SystemExit('send kampanya satırı beklenen yerde bulunamadı')
s = s.replace(old, new, 1)

p.write_text(s, encoding='utf-8')
compile(s, str(p), 'exec')
print('Telegram campaign extraction + product-page campaign fallback OK')
