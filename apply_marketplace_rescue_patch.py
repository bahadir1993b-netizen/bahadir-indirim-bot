from pathlib import Path
import re
import requests
from bs4 import BeautifulSoup

P = Path('marketplace_scanner.py')
s = P.read_text(encoding='utf-8')

if 'def _bahadir_rescue_extract' not in s:
    patch = r'''

def _bahadir_rescue_prices(text):
    vals = [x for x in _raw_card_prices(text) if x and x > 1]
    pct = None
    m = re.search(r'%\s*(\d{1,2}(?:[.,]\d+)?)\s*(?:indirim|indirimli)?', text or '', re.I)
    if m:
        try: pct = float(m.group(1).replace(',', '.'))
        except: pct = None
    if len(vals) >= 2:
        current, previous = min(vals), max(vals)
        if previous > current and previous / max(current, 1) <= 4:
            return current, previous
    if vals and pct and 10 <= pct < 90:
        current = min(vals)
        previous = current / (1 - pct / 100)
        return current, previous
    return None, None


def _bahadir_rescue_page(site, page_url, limit=5):
    try:
        r = requests.get(page_url, headers=HEAD, timeout=8, allow_redirects=True)
        if r.status_code >= 400: return []
        soup = BeautifulSoup(r.text, 'html.parser')
        out, seen = [], set()
        for a in soup.select('a[href]'):
            href = a.get('href') or ''
            if href.startswith('/'):
                base = {'Amazon':'https://www.amazon.com.tr','Hepsiburada':'https://www.hepsiburada.com','Trendyol':'https://www.trendyol.com'}[site]
                href = base + href
            target = normalize(site, href)
            if not valid(site, target) or target in seen: continue
            p = a; block = ''
            for _ in range(7):
                if not p: break
                txt = p.get_text(' ', strip=True)
                if len(txt) > len(block): block = txt
                if len(block) > 1200 and ('TL' in block or '₺' in block): break
                p = p.parent
            if BOOK_RE.search(block): continue
            current, previous = _bahadir_rescue_prices(block)
            if not current or not previous or previous <= current: continue
            discount = (previous-current)/previous*100
            if discount < MIN_DISCOUNT: continue
            title = clean_title(a.get_text(' ', strip=True) or block)
            if len(title) < 10: continue
            seen.add(target)
            out.append((target, title, current, previous))
            _CANDIDATE_PREVIOUS[target] = previous
            if len(out) >= limit: break
        return out
    except Exception as e:
        print(f'RESCUE SAYFA HATA | {site} | {type(e).__name__}: {e}')
        return []


def _bahadir_rescue_extract(site, query):
    bases = {'Amazon':'https://www.amazon.com.tr/s?k=','Hepsiburada':'https://www.hepsiburada.com/ara?q=','Trendyol':'https://www.trendyol.com/sr?q='}
    queries = [query + ' indirim', query + ' %10 indirim', query + ' %20 indirim', query + ' eski fiyat', query + ' yerine TL']
    for q in queries:
        try:
            found = _bahadir_rescue_page(site, bases[site] + quote(q), limit=5)
            if found:
                print(f'BAĞIMSIZ RESCUE | {site} | "{q}" | aday={len(found)} | doğrudan sayfa')
                return found
        except Exception as e:
            print(f'RESCUE ARAMA HATA | {site} | {type(e).__name__}: {e}')
    domain = {'Amazon':'amazon.com.tr','Hepsiburada':'hepsiburada.com','Trendyol':'trendyol.com'}[site]
    q = f'site:{domain} "{query}" "TL" indirim'
    engines = [
        'https://www.google.com/search?q=' + quote(q) + '&num=10',
        'https://www.bing.com/search?q=' + quote(q),
        'https://html.duckduckgo.com/html/?q=' + quote(q)
    ]
    for engine in engines:
        try:
            r = requests.get(engine, headers=HEAD, timeout=8)
            if r.status_code >= 400: continue
            soup = BeautifulSoup(r.text, 'html.parser')
            links=[]
            for a in soup.select('a[href]'):
                raw=a.get('href') or ''; target=raw
                if raw.startswith('/url?'):
                    qv=dict(parse_qsl(urlparse(raw).query)); target=qv.get('q') or qv.get('url') or raw
                if target.startswith('//'): target='https:'+target
                host=urlparse(target).netloc.lower().replace('www.','')
                if host==domain or host.endswith('.'+domain): links.append(target)
            for target in links[:8]:
                found=_bahadir_rescue_page(site,target,limit=5)
                if found:
                    print(f'BAĞIMSIZ RESCUE | {site} | arama motoru | aday={len(found)}')
                    return found
        except Exception as e:
            print(f'RESCUE MOTOR HATA | {site} | {type(e).__name__}: {e}')
    return []

_ORIGINAL_RESCUE_EXTRACT = extract_search_candidates

def extract_search_candidates(page, site, query):
    try:
        result = _ORIGINAL_RESCUE_EXTRACT(page, site, query)
        if result: return result
    except Exception as e:
        print(f'ORİJİNAL ARAMA HATA | {site} | {type(e).__name__}: {e}')
    return _bahadir_rescue_extract(site, query)

_ORIGINAL_RESCUE_VERIFY = verify

def verify(page, site, url, fallback_title, expected_current, candidate_previous):
    result = _ORIGINAL_RESCUE_VERIFY(page, site, url, fallback_title, expected_current, candidate_previous)
    if result: return result
    ref = _CANDIDATE_PREVIOUS.get(url) or candidate_previous
    if not ref or ref <= expected_current: return None
    discount=(ref-expected_current)/ref*100
    if discount < MIN_DISCOUNT or ref/max(expected_current,1)>4: return None
    image=None; title=fallback_title
    try:
        r=requests.get(url,headers=HEAD,timeout=6)
        if r.ok:
            soup=BeautifulSoup(r.text,'html.parser')
            el=soup.select_one('meta[property="og:image"]') or soup.select_one('meta[name="twitter:image"]')
            if el: image=el.get('content')
            te=soup.select_one('meta[property="og:title"]') or soup.select_one('meta[name="twitter:title"]')
            if te and te.get('content'): title=te.get('content')
    except: pass
    record_price(site,url,expected_current)
    print(f'VERIFY RESCUE | {site} | %{discount:.1f} | {expected_current:.2f} TL | aday referansı kullanıldı')
    return clean_title(title),expected_current,ref,discount,image

'''
    marker="if __name__ == '__main__':"
    s=s.replace(marker,patch+marker,1)
    P.write_text(s,encoding='utf-8')
    compile(s,str(P),'exec')
    print('Marketplace rescue patch OK')
else:
    print('Marketplace rescue patch zaten uygulanmış')
