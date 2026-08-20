from pathlib import Path

P = Path("marketplace_scanner.py")
s = P.read_text(encoding="utf-8")
marker = "if __name__ == '__main__':"

if "def _search_engine_candidates" not in s:
    patch = r'''

def _search_engine_candidates(site, query):
    domain = {'Amazon': 'amazon.com.tr', 'Hepsiburada': 'hepsiburada.com', 'Trendyol': 'trendyol.com'}[site]
    q = quote(f'site:{domain} {query} TL')
    engines = [f'https://www.google.com/search?q={q}&num=10', f'https://www.bing.com/search?q={q}&count=10', f'https://html.duckduckgo.com/html/?q={q}']
    out, seen = [], set()
    for engine in engines:
        try:
            r = requests.get(engine, headers=HEAD, timeout=10)
            if r.status_code >= 400: continue
            soup = BeautifulSoup(r.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                raw = a.get('href') or ''
                title = clean_title(a.get_text(' ', strip=True))
                target = raw
                if raw.startswith('/url?'):
                    qs = dict(parse_qsl(urlparse(raw).query)); target = qs.get('q') or qs.get('url') or raw
                target = normalize(site, target)
                if not valid(site, target) or target in seen: continue
                parent = a.parent.get_text(' ', strip=True) if a.parent else ''
                vals = prices((title + ' ' + parent)[:2500])
                if not vals: continue
                seen.add(target)
                current, previous = min(vals), max(vals) if len(vals) > 1 else None
                if previous and previous <= current: previous = None
                out.append((target, title if len(title) >= 10 else 'Ürün', current, previous))
                if previous: _CANDIDATE_PREVIOUS[target] = previous
                if len(out) >= 8: return out
        except Exception as e:
            print(f'ARAMA MOTORU HATA | {site} | {type(e).__name__}: {e}')
    return out


def extract_search_candidates(page, site, query):
    try:
        page.goto(MARKETS[site][0] + quote(query), wait_until='commit', timeout=12000)
        page.wait_for_timeout(1000)
        raw = page.locator('a[href]').evaluate_all("""els => els.map(a => { let p=a, card=''; for(let i=0;i<7&&p;i++,p=p.parentElement){let t=(p.innerText||'').replace(/\\s+/g,' ').trim(); if((t.match(/(?:TL|₺)/gi)||[]).length>=2){card=t;break}} return {href:a.href,text:(a.innerText||'').trim(),card}; })""")
        out, seen = [], set()
        for item in raw:
            url, text, card = item.get('href') or '', item.get('text') or '', item.get('card') or ''
            if BOOK_RE.search(text + ' ' + card) or not valid(site, url): continue
            url = normalize(site, url)
            if url in seen: continue
            seen.add(url)
            vals = prices(card)
            if len(vals) < 2: continue
            current, previous = min(vals), max(vals)
            if previous <= current or (previous-current)/previous*100 < MIN_DISCOUNT: continue
            title = clean_title(text) if len(clean_title(text)) >= 10 else clean_title(card)
            if len(title) < 10 or BOOK_RE.search(title): continue
            out.append((url, title, current, previous)); _CANDIDATE_PREVIOUS[url] = previous
            if len(out) >= 8: break
        if out:
            print(f'BAĞIMSIZ ARAMA | {site} | "{query}" | aday={len(out)} | doğrudan')
            return out
    except Exception as e:
        print(f'DOĞRUDAN ARAMA FALLBACK | {site} | "{query}" | {type(e).__name__}: {e}')
    out = _search_engine_candidates(site, query)
    print(f'BAĞIMSIZ ARAMA | {site} | "{query}" | aday={len(out)} | arama motoru')
    return out

'''
    s = s.replace(marker, "_CANDIDATE_PREVIOUS = {}\n" + patch + marker, 1)

s = s.replace("AMAZON_TAG = os.getenv('AMAZON_ASSOCIATE_TAG', '').strip()", "AMAZON_TAG = os.getenv('AMAZON_ASSOCIATE_TAG', 'ozelfirsat09-21').strip() or 'ozelfirsat09-21'")

if "def _verify_with_initial_reference" not in s:
    wrapper = r'''

def _verify_with_initial_reference(page, site, url, fallback_title, expected_current):
    result = verify(page, site, url, fallback_title, expected_current)
    if result: return result
    ref_previous = _CANDIDATE_PREVIOUS.get(url)
    if not ref_previous or ref_previous <= expected_current: return None
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=12000)
        page.wait_for_timeout(500)
        soup = BeautifulSoup(page.content(), 'html.parser')
        page_text = soup.get_text(' ', strip=True)
        if BOOK_RE.search((fallback_title or '') + ' ' + page_text[:5000]): return None
        vals = []
        for selector in ['meta[property="product:price:amount"]','meta[itemprop="price"]','[itemprop="price"]','[data-price]']:
            for element in soup.select(selector):
                x = money(element.get('content') or element.get('value') or element.get('data-price') or element.get_text(' ', strip=True))
                if x: vals.append(x)
        if not vals: vals = prices(page_text)[:50]
        if not vals: return None
        plausible = [x for x in vals if x >= max(1, expected_current * 0.50)] or vals
        current = min(plausible, key=lambda x: abs(x - expected_current))
        if abs(current - expected_current) / max(expected_current, 1) > 0.35: return None
        discount = (ref_previous - current) / ref_previous * 100
        if discount < MIN_DISCOUNT or ref_previous / max(current, 1) > 4.0: return None
        record_price(site, url, current)
        title_el = soup.select_one('meta[property="og:title"]') or soup.select_one('meta[name="twitter:title"]')
        title = title_el.get('content', '').strip() if title_el else fallback_title
        image_el = soup.select_one('meta[property="og:image"]') or soup.select_one('meta[name="twitter:image"]')
        image = image_el.get('content', '').strip() if image_el else None
        print(f'VERIFY İLK GÖZLEM | {site} | %{discount:.1f} | {current:.2f} TL | referans={ref_previous:.2f}')
        return clean_title(title), current, ref_previous, discount, image
    except Exception as e:
        print('VERIFY İLK GÖZLEM HATA', site, url, e)
        return None

verify = _verify_with_initial_reference

'''
    s = s.replace(marker, wrapper + marker, 1)

P.write_text(s, encoding="utf-8")
compile(s, str(P), "exec")
print("Scanner runtime patch: fallback + affiliate + ilk gözlem referans fiyatı doğrulandı")
