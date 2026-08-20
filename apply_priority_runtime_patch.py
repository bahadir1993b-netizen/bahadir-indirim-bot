from pathlib import Path
import re

P = Path('telegram_sources.py')
s = P.read_text(encoding='utf-8')

# Kaynak sırası: yüksek sinyalli reklam/fırsat kanalları önce.
new_sources = "SOURCES={'AmazonOzel':'amazonozel','IndirimAlarmi':'indirimalarmi_tr','YurticiFirsat':'yurticifirsat','FirsatDolu':'firsatdolu','IndirimBuluyoruz':'indirimbuldumtg','OzelFirsatlar':'ozelfirsat','FirsatMerkezi':'firsatmerkez','IndirimDeal':'indirimdeal','FirsatZ':'firsatz','OnuAl':'onual_firsat','EnesOzen':'enesozen'}"
s, n = re.subn(r"SOURCES=\{.*?\}", new_sources, s, count=1, flags=re.S)
if n != 1:
    raise SystemExit('SOURCES bulunamadı')

# Test aşamasında düşük eşik: %2 ve üzeri fırsatlar gönderilebilir.
s = re.sub(r"MIN_DISCOUNT=6\.0", "MIN_DISCOUNT=2.0", s, count=1)
s = s.replace("MAX_AGE=30", "MAX_AGE=30\nSOURCE_PRIORITY=True\nSOURCE_LIMIT=12")

if '_PriorityExecutor' not in s:
    marker = "from playwright.sync_api import sync_playwright\n"
    inject = '''from concurrent.futures import Future\n\nclass _PriorityExecutor:\n    def __init__(self, *args, **kwargs):\n        pass\n    def __enter__(self): return self\n    def __exit__(self, exc_type, exc, tb): return False\n    def submit(self, fn, *args, **kwargs):\n        f = Future()\n        try:\n            f.set_result(fn(*args, **kwargs))\n        except Exception as e:\n            f.set_exception(e)\n        return f\n\nThreadPoolExecutor = _PriorityExecutor\n'''
    s = s.replace(marker, marker + inject, 1)

s = s.replace("class _PriorityExecutor:\n    def __enter__", "class _PriorityExecutor:\n    def __init__(self, *args, **kwargs): pass\n    def __enter__")

new_check = '''def marketplace_price_check(s,u,expected):\n    try:\n        r=requests.get(u,headers=HEAD,timeout=5)\n        if r.status_code < 400:\n            soup=BeautifulSoup(r.text,'html.parser'); vals=[]\n            for sel in ['meta[property="product:price:amount"]','meta[itemprop="price"]','[itemprop="price"]','[data-price]']:\n                for el in soup.select(sel):\n                    x=money(el.get('content') or el.get('value') or el.get_text(' ',strip=True) or el.get('data-price'))\n                    if x: vals.append(x)\n            if vals and expected:\n                current=min(vals,key=lambda x:abs(x-expected))\n                if abs(current-expected)/max(expected,1)<=0.35:\n                    return current,max((x for x in vals if x>current),default=None)\n        if expected and 0 < expected < 10000000:\n            return expected,None\n    except Exception:\n        if expected and 0 < expected < 10000000:\n            return expected,None\n    return None,None\n'''
s, n = re.subn(r"def marketplace_price_check\(s,u,expected\):.*?(?=\ndef coupon_code\()", new_check, s, count=1, flags=re.S)
if n != 1:
    raise SystemExit('marketplace_price_check bulunamadı')

coupon_patch = r'''\ndef coupon_code(text):\n    pats=[\n        r'\\b(?:KOD|KODU|KUPON|KUPON KODU|PROMOSYON(?: KODU)?)\\s*[:=\\-]?\\s*([A-ZÇĞİÖŞÜ0-9][A-ZÇĞİÖŞÜ0-9_-]{4,23})\\b',\n        r'\\b([A-ZÇĞİÖŞÜ0-9][A-ZÇĞİÖŞÜ0-9_-]{4,23})\\s+(?:KOD(?:U)?|KUPON(?:U)?)\\b'\n    ]\n    bad={'INDIRIM','KAMPANYA','FIRSAT','AMAZON','HEPSIBURADA','TRENDYOL','UYLA','KOD','KUPON','PROMO'}\n    for pat in pats:\n        for m in re.finditer(pat,text or '',re.I):\n            code=m.group(1).upper()\n            if code in bad or code.isdigit() or not re.search(r'[A-ZÇĞİÖŞÜ]',code):\n                continue\n            return code\n    return None\n'''
s, n = re.subn(r"\ndef coupon_code\(text\):.*?(?=\ndef seen\()", coupon_patch, s, count=1, flags=re.S)
if n != 1:
    raise SystemExit('coupon_code bulunamadı')

if "for b in blocks[:SOURCE_LIMIT]:" not in s:
    s = s.replace("for b in blocks:", "for b in blocks[:SOURCE_LIMIT]:")

P.write_text(s, encoding='utf-8')
print('Priority patch OK | kaynak önceliği | sıralı gönderim | canlı fiyat fallback | %2 eşik | sahte kupon filtresi')
