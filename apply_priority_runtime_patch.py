from pathlib import Path
import re

P = Path('telegram_sources.py')
s = P.read_text(encoding='utf-8')

new_sources = "SOURCES={'AmazonOzel':'amazonozel','IndirimAlarmi':'indirimalarmi_tr','YurticiFirsat':'yurticifirsat','FirsatDolu':'firsatdolu','IndirimBuluyoruz':'indirimbuldumtg','OzelFirsatlar':'ozelfirsat','FirsatMerkezi':'firsatmerkez','IndirimDeal':'indirimdeal','FirsatZ':'firsatz','OnuAl':'onual_firsat','EnesOzen':'enesozen'}"
s, n = re.subn(r"SOURCES=\{.*?\}", new_sources, s, count=1, flags=re.S)
if n != 1: raise SystemExit('SOURCES bulunamadı')
s = re.sub(r"MIN_DISCOUNT=6\.0", "MIN_DISCOUNT=2.0", s, count=1)
s = re.sub(r"MAX_AGE=\d+", "MAX_AGE=45", s, count=1)
if 'SOURCE_PRIORITY=True' not in s:
    s = s.replace('MAX_AGE=45', 'MAX_AGE=45\nSOURCE_PRIORITY=True\nSOURCE_LIMIT=12', 1)

if '_PriorityExecutor' not in s:
    marker = "from playwright.sync_api import sync_playwright\n"
    inject = '''from concurrent.futures import Future\n\nclass _PriorityExecutor:\n    def __init__(self, *args, **kwargs): pass\n    def __enter__(self): return self\n    def __exit__(self, exc_type, exc, tb): return False\n    def submit(self, fn, *args, **kwargs):\n        f=Future()\n        try: f.set_result(fn(*args, **kwargs))\n        except Exception as e: f.set_exception(e)\n        return f\n\nThreadPoolExecutor = _PriorityExecutor\n'''
    s = s.replace(marker, marker + inject, 1)

# Live fiyat sadece doğrulama için kullanılır. Kaynak fiyatına kör fallback yok.
new_check = '''def marketplace_price_check(s,u,expected):\n    try:\n        r=requests.get(u,headers=HEAD,timeout=7)\n        if r.status_code >= 400:\n            return None,None\n        soup=BeautifulSoup(r.text,'html.parser'); vals=[]\n        for sel in ['meta[property="product:price:amount"]','meta[itemprop="price"]','[itemprop="price"]','[data-price]']:\n            for el in soup.select(sel):\n                x=money(el.get('content') or el.get('value') or el.get_text(' ',strip=True) or el.get('data-price'))\n                if x: vals.append(x)\n        if vals and expected:\n            current=min(vals,key=lambda x:abs(x-expected))\n            if abs(current-expected)/max(expected,1)<=0.35:\n                return current,max((x for x in vals if x>current),default=None)\n        return None,None\n    except Exception:\n        return None,None\n'''
s, n = re.subn(r"def marketplace_price_check\(s,u,expected\):.*?(?=\ndef coupon_code\()", new_check, s, count=1, flags=re.S)
if n != 1: raise SystemExit('marketplace_price_check bulunamadı')

coupon_patch = '''def coupon_code(text):\n    pats=[\n        r'\\b(?:KOD|KODU|KUPON|KUPON KODU|PROMOSYON(?: KODU)?)\\s*[:=\\-]?\\s*([A-ZÇĞİÖŞÜ0-9][A-ZÇĞİÖŞÜ0-9_-]{4,23})\\b',\n        r'\\b([A-ZÇĞİÖŞÜ0-9][A-ZÇĞİÖŞÜ0-9_-]{4,23})\\s+(?:KOD(?:U)?|KUPON(?:U)?)\\b'\n    ]\n    bad={'INDIRIM','KAMPANYA','FIRSAT','AMAZON','HEPSIBURADA','TRENDYOL','UYLA','KOD','KUPON','PROMO'}\n    for pat in pats:\n        for m in re.finditer(pat,text or '',re.I):\n            code=m.group(1).upper()\n            if code in bad or code.isdigit() or not re.search(r'[A-ZÇĞİÖŞÜ]',code): continue\n            return code\n    return None\n'''
s, n = re.subn(r"def coupon_code\(text\):.*?(?=\ndef seen\()", lambda m: coupon_patch, s, count=1, flags=re.S)
if n != 1: raise SystemExit('coupon_code bulunamadı')

# Kampanya fiyatlarını ortak biçimde anla: adet başı + 3 al 2 öde vb.
campaign_patch = '''def source_pair(t):\n    text=t or ''\n    # Açık birim fiyat: "4 adet alımda ... adet başı 127 TL"\n    unit_patterns=[\n        r'(?:adet|ürün|parça)\\s*başı\\s*[:=]?\\s*(\\d[\\d.,]*)\\s*(?:TL|₺)',\n        r'(?:adet|ürün|parça)\\s*(?:fiyatı|fiyati)\\s*[:=]?\\s*(\\d[\\d.,]*)\\s*(?:TL|₺)',\n        r'(?:\\d+)\\s*adet.*?(\\d[\\d.,]*)\\s*(?:TL|₺)\\s*(?:\\(\\s*adet\\s*başı|/\\s*adet)'\n    ]\n    for pat in unit_patterns:\n        m=re.search(pat,text,re.I|re.S)\n        if m:\n            unit=money(m.group(1))\n            if unit:\n                p=prices(text); candidates=[x for x in p if x and x>unit*1.02]\n                baseline=min(candidates,key=lambda x:abs(x-unit)) if candidates else None\n                return unit,baseline\n\n    # "3 al 2 öde 51 TL" -> ödenen 2 adet 51 TL, 3 adet teslim;\n    # efektif birim maliyet = 51 / 3. Böylece kampanya gerçek bir fırsat olarak\n    # değerlendirilir; tekli canlı fiyat daha sonra marketplace doğrulamasında kontrol edilir.\n    m=re.search(r'(\\d+)\\s*al\\s*(\\d+)\\s*öde\\s*[:=]?\\s*(\\d[\\d.,]*)\\s*(?:TL|₺)',text,re.I)\n    if m:\n        buy=int(m.group(1)); pay=int(m.group(2)); total=money(m.group(3))\n        if buy>0 and 0<pay<=buy and total:\n            unit=total/buy\n            p=prices(text); candidates=[x for x in p if x and x>unit*1.02 and x!=total]\n            baseline=min(candidates,key=lambda x:abs(x-unit)) if candidates else None\n            return unit,baseline\n\n    p=prices(text)\n    return (p[0],None) if p else (None,None)\n'''
s, n = re.subn(r"def source_pair\(t\):.*?(?=\ndef coupon_savings\()", lambda m: campaign_patch, s, count=1, flags=re.S)
if n != 1: raise SystemExit('source_pair bulunamadı')

if "for b in blocks[:SOURCE_LIMIT]:" not in s:
    s = s.replace("for b in blocks:", "for b in blocks[:SOURCE_LIMIT]:")

P.write_text(s, encoding='utf-8')
print('Priority patch OK | %2 | MAX_AGE 45 | kampanya hesapları | canlı fiyat fallback yok')
