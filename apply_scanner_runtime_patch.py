from pathlib import Path

P = Path("marketplace_scanner.py")
s = P.read_text(encoding="utf-8")
marker = "if __name__ == '__main__':"

# Marketplace arama sayfaları GitHub Actions'ta zaman zaman download/redirect/chrome-error
# döndürebildiği için doğrudan aramaya bağımlı kalma. Arama motorlarından ürün URL'si bul,
# ardından mevcut verify() ile gerçek ürün sayfasını doğrula.
if "def _search_engine_candidates" not in s:
    patch = r'''

def _search_engine_candidates(site, query):
    domain = {'Amazon': 'amazon.com.tr', 'Hepsiburada': 'hepsiburada.com', 'Trendyol': 'trendyol.com'}[site]
    q = quote(f'site:{domain} {query} TL')
    engines = [
        f'https://www.google.com/search?q={q}&num=10',
        f'https://www.bing.com/search?q={q}&count=10',
        f'https://html.duckduckgo.com/html/?q={q}',
    ]
    out, seen = [], set()
    for engine in engines:
        try:
            r = requests.get(engine, headers=HEAD, timeout=10)
            if r.status_code >= 400:
                continue
            soup = BeautifulSoup(r.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                raw = a.get('href') or ''
                title = clean_title(a.get_text(' ', strip=True))
                target = raw
                if raw.startswith('/url?'):
                    qs = dict(parse_qsl(urlparse(raw).query))
                    target = qs.get('q') or qs.get('url') or raw
                target = normalize(site, target)
                if not valid(site, target) or target in seen:
                    continue
                parent = a.parent.get_text(' ', strip=True) if a.parent else ''
                card = (title + ' ' + parent)[:2500]
                vals = prices(card)
                if not vals:
                    continue
                seen.add(target)
                current = min(vals)
                previous = max(vals) if len(vals) > 1 else None
                if previous and previous <= current:
                    previous = None
                out.append((target, title if len(title) >= 10 else 'Ürün', current, previous))
                if len(out) >= 8:
                    return out
        except Exception as e:
            print(f'ARAMA MOTORU HATA | {site} | {type(e).__name__}: {e}')
    return out


def extract_search_candidates(page, site, query):
    # Mevcut scanner bu fonksiyonu (page, site, query) olarak çağırıyor.
    # Doğrudan marketplace araması çalışmazsa arama motoru fallback'i kullanılır.
    try:
        page_url = MARKETS[site][0] + quote(query)
        page.goto(page_url, wait_until='commit', timeout=12000)
        page.wait_for_timeout(1000)
        raw = page.locator('a[href]').evaluate_all("""els => els.map(a => { let p=a, card=''; for(let i=0;i<7&&p;i++,p=p.parentElement){let t=(p.innerText||'').replace(/\\s+/g,' ').trim(); if((t.match(/(?:TL|₺)/gi)||[]).length>=2){card=t;break}} return {href:a.href,text:(a.innerText||'').trim(),card}; })""")
        out, seen = [], set()
        for item in raw:
            url = item.get('href') or ''
            text = item.get('text') or ''
            card = item.get('card') or ''
            if BOOK_RE.search(text + ' ' + card) or not valid(site, url):
                continue
            url = normalize(site, url)
            if url in seen:
                continue
            seen.add(url)
            vals = prices(card)
            if len(vals) < 2:
                continue
            current, previous = min(vals), max(vals)
            if previous <= current:
                continue
            discount = (previous - current) / previous * 100
            if discount < MIN_DISCOUNT:
                continue
            title = clean_title(text) if len(clean_title(text)) >= 10 else clean_title(card)
            if len(title) < 10 or BOOK_RE.search(title):
                continue
            out.append((url, title, current, previous))
            if len(out) >= 8:
                break
        if out:
            print(f'BAĞIMSIZ ARAMA | {site} | "{query}" | aday={len(out)} | doğrudan')
            return out
    except Exception as e:
        print(f'DOĞRUDAN ARAMA FALLBACK | {site} | "{query}" | {type(e).__name__}: {e}')
    out = _search_engine_candidates(site, query)
    print(f'BAĞIMSIZ ARAMA | {site} | "{query}" | aday={len(out)} | arama motoru')
    return out

'''
    if marker not in s:
        raise SystemExit("main marker bulunamadı")
    s = s.replace(marker, patch + marker, 1)

# Affiliate tag'in boş kalması durumunda bile doğru Amazon etiketi kullanılsın.
s = s.replace("AMAZON_TAG = os.getenv('AMAZON_ASSOCIATE_TAG', '').strip()", "AMAZON_TAG = os.getenv('AMAZON_ASSOCIATE_TAG', 'ozelfirsat09-21').strip() or 'ozelfirsat09-21'")

P.write_text(s, encoding="utf-8")
compile(s, str(P), "exec")
print("Scanner runtime patch: arama motoru fallback + Amazon affiliate tag doğrulandı")
