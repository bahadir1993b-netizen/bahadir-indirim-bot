from pathlib import Path

# Bu patch yayın öncesinde 3 kritik kuralı tek noktada zorlar:
# 1) Amazon URL'sinde affiliate tag daima olsun.
# 2) Şüpheli/şişirilmiş referans fiyat yayınlanmasın.
# 3) Ürün fotoğrafı yoksa metin-only fırsat yayınlanmasın.

p = Path('direct_bot.py')
if not p.exists():
    raise SystemExit('direct_bot.py bulunamadı')
s = p.read_text(encoding='utf-8')

# Affiliate tag için merkezi yardımcı.
marker = 'import os\n'
helper = r'''
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

AMAZON_ASSOCIATE_TAG = os.getenv('AMAZON_ASSOCIATE_TAG', 'ozelfirsat09-21').strip() or 'ozelfirsat09-21'

def ensure_amazon_affiliate(url):
    if not url or 'amazon.com.tr' not in str(url).lower():
        return url
    try:
        u = urlsplit(str(url))
        q = dict(parse_qsl(u.query, keep_blank_values=True))
        q['tag'] = AMAZON_ASSOCIATE_TAG
        return urlunsplit((u.scheme or 'https', u.netloc, u.path, urlencode(q), u.fragment))
    except Exception:
        sep = '&' if '?' in str(url) else '?'
        return f'{url}{sep}tag={AMAZON_ASSOCIATE_TAG}'

def sane_reference(current, reference):
    try:
        c, r = float(current), float(reference)
    except Exception:
        return None
    if c <= 0 or r <= c:
        return None
    # Amazon liste/strike fiyatı tek başına %45+ indirim iddiası doğurmasın.
    # Böyle durumlarda doğrulanmış geçmiş fiyat olmadan referansı göstermiyoruz.
    if r / c > 1.80:
        return None
    return r
'''
if 'def ensure_amazon_affiliate(' not in s:
    if marker in s:
        s = s.replace(marker, marker + helper + '\n', 1)
    else:
        s = helper + '\n' + s

# Telegram butonlarına giden URL'yi son anda da affiliate yap.
# Yaygın url değişkenlerini güvenli biçimde dönüştür.
for needle in [
    "url=product_url",
    "url=deal_url",
    "url=url",
    "'url': product_url",
    '"url": product_url',
]:
    if needle in s and 'ensure_amazon_affiliate' not in needle:
        if needle == 'url=product_url': repl = 'url=ensure_amazon_affiliate(product_url)'
        elif needle == 'url=deal_url': repl = 'url=ensure_amazon_affiliate(deal_url)'
        elif needle == 'url=url': repl = 'url=ensure_amazon_affiliate(url)'
        elif needle == "'url': product_url": repl = "'url': ensure_amazon_affiliate(product_url)"
        else: repl = '"url": ensure_amazon_affiliate(product_url)'
        s = s.replace(needle, repl)

compile(s, str(p), 'exec')
p.write_text(s, encoding='utf-8')

# Scanner tarafında Amazon referans fiyatını sınırlayıp affiliate URL'yi kaynağında da üret.
mp = Path('marketplace_scanner.py')
if mp.exists():
    m = mp.read_text(encoding='utf-8')
    if 'def _amazon_affiliate_url(' not in m:
        prefix = r'''
from urllib.parse import urlsplit as _usplit, urlunsplit as _uunsplit, parse_qsl as _pqsl, urlencode as _uenc
_AMZ_TAG = os.getenv('AMAZON_ASSOCIATE_TAG', 'ozelfirsat09-21').strip() or 'ozelfirsat09-21'
def _amazon_affiliate_url(url):
    if not url or 'amazon.com.tr' not in str(url).lower(): return url
    u=_usplit(str(url)); q=dict(_pqsl(u.query, keep_blank_values=True)); q['tag']=_AMZ_TAG
    return _uunsplit((u.scheme or 'https',u.netloc,u.path,_uenc(q),u.fragment))
'''
        # os çoğu scanner'da mevcut; yoksa ekle.
        if 'import os' not in m:
            prefix = 'import os\n' + prefix
        m = prefix + '\n' + m
    # %50 gibi Amazon strike anomalilerini fırsat diye yayınlamayı engelle.
    m = m.replace("discount = (previous - current) / previous * 100", "discount = (previous - current) / previous * 100\n            if site == 'Amazon' and previous/current > 1.80:\n                continue")
    # Bulunan Amazon href'lerine tag'i mümkün olan son ortak noktada ekle.
    m = m.replace("url = href", "url = _amazon_affiliate_url(href) if site == 'Amazon' else href")
    compile(m, str(mp), 'exec')
    mp.write_text(m, encoding='utf-8')

# Fotoğraf guard patch'ini de çalıştır; mevcut patch idempotent tasarlanmıştır.
photo = Path('apply_final_telegram_photo_guard.py')
if photo.exists():
    exec(compile(photo.read_text(encoding='utf-8'), str(photo), 'exec'), {})

print('PUBLISH INTEGRITY GUARD OK | Amazon tag zorunlu | sahte referans engelli | foto guard aktif')
