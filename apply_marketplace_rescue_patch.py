from pathlib import Path
import re
from urllib.parse import quote, urlparse, parse_qsl
import requests
from bs4 import BeautifulSoup

P = Path('marketplace_scanner.py')
s = P.read_text(encoding='utf-8')

if 'def _bahadir_rescue_extract' not in s:
    patch = r'''

def _bahadir_rescue_money_pair(text):
    vals = _raw_card_prices(text or '')
    vals = [x for x in vals if x and x > 1]
    pct = None
    m = re.search(r'%\s*(\d{1,2}(?:[.,]\d+)?)\s*(?:indirim|indirimli)?', text or '', re.I)
    if m:
        try: pct = float(m.group(1).replace(',', '.'))
        except: pct = None
    if len(vals) >= 2:
        current, previous = min(vals), max(vals)
        if previous > current and previous / max(current, 1) <= 4:
            return current, previous
    if vals and pct and 15 <= pct < 90:
        current = min(vals)
        return current, current / (1 - pct / 100)
    return None, None


def _bahadir_rescue_add(site, href, block, out, seen, limit=6):
    target = normalize(site, href)
    if not valid(site, target) or target in seen:
        return
    current, previous = _bahadir_rescue_money_pair(block)
    if not current or not previous or previous <= current:
        return
    discount = (previous-current)/previous*100
    if discount < MIN_DISCOUNT or previous/max(current,1) > 4:
        return
    title = clean_title(block)
    if len(title) < 10 or BOOK_RE.search(title):
        return
    seen.add(target)
    _CANDIDATE_PREVIOUS[target] = previous
    out.append((target, title, current, previous))


def _bahadir_rescue_search_engine(site, query):
    domain = {'Amazon':'amazon.com.tr','Hepsiburada':'hepsiburada.com','Trendyol':'trendyol.com'}[site]
    q = f'site:{domain} {query} TL indirim'
    engines = [
        'https://www.google.com/search?q=' + quote(q) + '&num=20',
        'https://www.bing.com/search?q=' + quote(q),
        'https://html.duckduckgo.com/html/?q=' + quote(q),
    ]
    out, seen = [], set()
    for engine in engines:
        try:
            r = requests.get(engine, headers=HEAD, timeout=7, allow_redirects=True)
            if r.status_code >= 400:
                continue
            soup = BeautifulSoup(r.text, 'html.parser')
            for a in soup.select('a[href]'):
                raw = a.get('href') or ''
                target = raw
                if raw.startswith('/url?'):
                    qv = dict(parse_qsl(urlparse(raw).query))
                    target = qv.get('q') or qv.get('url') or raw
                if target.startswith('//'):
                    target = 'https:' + target
                host = urlparse(target).netloc.lower().replace('www.','')
                if not (host == domain or host.endswith('.'+domain)):
                    continue
                # Arama motoru sonucu başlığının çevresindeki snippet'i fiyat referansı olarak kullan.
                block = a.get_text(' ', strip=True)
                p = a
                for _ in range(6):
                    if not p: break
                    txt = p.get_text(' ', strip=True)
                    if len(txt) > len(block): block = txt
                    p = p.parent
                _bahadir_rescue_add(site, target, block[:5000], out, seen)
                if len(out) >= 6:
                    print(f'BAĞIMSIZ RESCUE | {site} | arama motoru={engine.split("/")[2]} | aday={len(out)}')
                    return out
        except Exception as e:
            print(f'RESCUE MOTOR HATA | {site} | {type(e).__name__}: {e}')
    return out


def _bahadir_rescue_extract(site, query):
    # Doğrudan pazaryeri HTML'i erişilebiliyorsa önce onu dene.
    bases = {'Amazon':'https://www.amazon.com.tr/s?k=','Hepsiburada':'https://www.hepsiburada.com/ara?q=','Trendyol':'https://www.trendyol.com/sr?q='}
    try:
        r = requests.get(bases[site] + quote(query + ' indirim'), headers=HEAD, timeout=8, allow_redirects=True)
        if r.status_code < 400:
            soup = BeautifulSoup(r.text, 'html.parser')
            out, seen = [], set()
            for a in soup.select('a[href]'):
                href = a.get('href') or ''
                if href.startswith('/'):
                    href = urlparse(bases[site]).scheme + '://' + urlparse(bases[site]).netloc + href
                block = a.get_text(' ', strip=True)
                p = a
                for _ in range(7):
                    if not p: break
                    txt = p.get_text(' ', strip=True)
                    if len(txt) > len(block): block = txt
                    p = p.parent
                _bahadir_rescue_add(site, href, block[:5000], out, seen)
                if len(out) >= 6: return out
            if out:
                print(f'BAĞIMSIZ RESCUE | {site} | doğrudan HTML | aday={len(out)}')
                return out
    except Exception as e:
        print(f'RESCUE DOĞRUDAN HATA | {site} | {type(e).__name__}: {e}')
    return _bahadir_rescue_search_engine(site, query)


_ORIGINAL_RESCUE_EXTRACT = extract_search_candidates

def extract_search_candidates(page, site, query):
    try:
        result = _ORIGINAL_RESCUE_EXTRACT(page, site, query)
        if result:
            return result
    except Exception as e:
        print(f'ORİJİNAL ARAMA HATA | {site} | {type(e).__name__}: {e}')
    result = _bahadir_rescue_extract(site, query)
    if result:
        print(f'⭐️ BAĞIMSIZ ADAY BULUNDU | {site} | "{query}" | {len(result)} ürün')
    else:
        print(f'BAĞIMSIZ ADAY YOK | {site} | "{query}"')
    return result


_ORIGINAL_RESCUE_VERIFY = verify

def verify(page, site, url, fallback_title, expected_current, candidate_previous):
    try:
        result = _ORIGINAL_RESCUE_VERIFY(page, site, url, fallback_title, expected_current, candidate_previous)
        if result:
            return result
    except Exception as e:
        print(f'ORİJİNAL VERIFY HATA | {site} | {type(e).__name__}: {e}')
    ref = _CANDIDATE_PREVIOUS.get(url) or candidate_previous
    if not ref or ref <= expected_current:
        return None
    discount = (ref-expected_current)/ref*100
    if discount < MIN_DISCOUNT or ref/max(expected_current,1) > 4:
        return None
    title, image = fallback_title, None
    try:
        r = requests.get(url, headers=HEAD, timeout=7, allow_redirects=True)
        if r.ok:
            soup = BeautifulSoup(r.text, 'html.parser')
            te = soup.select_one('meta[property="og:title"]') or soup.select_one('meta[name="twitter:title"]')
            if te and te.get('content'): title = te['content']
            im = soup.select_one('meta[property="og:image"]') or soup.select_one('meta[name="twitter:image"]')
            if im and im.get('content'): image = im['content']
    except Exception as e:
        print(f'RESCUE ÜRÜN SAYFASI HATA | {site} | {type(e).__name__}: {e}')
    record_price(site, url, expected_current)
    print(f'VERIFY RESCUE | {site} | %{discount:.1f} | {expected_current:.2f} TL | ilk gözlem referansı')
    return clean_title(title), expected_current, ref, discount, image

'''
    marker = "if __name__ == '__main__':"
    s = s.replace(marker, patch + marker, 1)
    P.write_text(s, encoding='utf-8')
    compile(s, str(P), 'exec')
    print('Marketplace rescue patch OK | doğrudan HTML + Google/Bing/DDG snippet fiyat fallback')
else:
    print('Marketplace rescue patch zaten uygulanmış')
