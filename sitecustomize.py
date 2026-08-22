"""Runtime marketplace extensions.

Python imports sitecustomize automatically.  Keep marketplace support here so all
collectors see the same extra stores without duplicating parsing logic.
"""
try:
    import re
    import requests
    from urllib.parse import urlparse
    import telegram_sources as ts

    # N11 fırsatları güvenilir Telegram kaynaklarında sık geçiyor. Daha önce
    # MARKET yalnız Amazon/HB/Trendyol içerdiği için N11 mesajları process()
    # aşamasına dahi girmeden eleniyordu.
    ts.MARKET['n11.com'] = 'N11'
    ts.SHORT.update({
        'n11.com': 'N11',
        'www.n11.com': 'N11',
    })

    _old_valid = ts.valid
    def valid_ext(site, url):
        if site != 'N11':
            return _old_valid(site, url)
        p = urlparse(url or '')
        host = p.netloc.lower().replace('www.', '')
        # N11 ürün URL'leri genel olarak /urun/<slug>-<id> biçiminde.
        return host.endswith('n11.com') and bool(re.search(r'/urun/[^/?#]+', p.path, re.I))
    ts.valid = valid_ext

    _old_http = ts.search_marketplace_http
    def search_http_ext(site, title):
        if site != 'N11':
            return _old_http(site, title)
        if not title:
            return None
        try:
            url = 'https://www.n11.com/arama?q=' + requests.utils.quote(' '.join(title.split())[:140])
            r = requests.get(url, headers=ts.HEAD, timeout=7)
            if r.status_code >= 400:
                return None
            from bs4 import BeautifulSoup
            candidates=[]
            for a in BeautifulSoup(r.text,'html.parser').select('a[href]'):
                u=ts.clean(a.get('href') or '')
                if ts.valid('N11',u):
                    candidates.append((ts.title_score(title,(a.get_text(' ',strip=True) or '')[:500]+' '+u),u))
            if candidates:
                score,u=max(candidates,key=lambda x:x[0])
                if score>=0.22:return ts.normalize('N11',u)
        except Exception:
            pass
        return None
    ts.search_marketplace_http = search_http_ext

    _old_browser = ts.search_marketplace_browser
    def search_browser_ext(page, site, title):
        if site != 'N11':
            return _old_browser(page, site, title)
        try:
            url='https://www.n11.com/arama?q='+requests.utils.quote(' '.join((title or '').split())[:140])
            page.goto(url,wait_until='domcontentloaded',timeout=9000); candidates=[]
            for a in page.locator('a[href]').all():
                u=ts.clean(a.get_attribute('href') or '')
                if ts.valid('N11',u):
                    candidates.append((ts.title_score(title,(a.inner_text() or '')[:500]+' '+u),u))
            if candidates:
                score,u=max(candidates,key=lambda x:x[0])
                if score>=0.22:return ts.normalize('N11',u)
        except Exception:
            pass
        return None
    ts.search_marketplace_browser = search_browser_ext
except Exception as exc:
    # Bir eklenti hatası ana botu durdurmasın; logda açıkça görülsün.
    print(f'Marketplace extension warning: {type(exc).__name__}: {exc}')
