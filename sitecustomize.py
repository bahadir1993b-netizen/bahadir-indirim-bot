"""Runtime marketplace extensions shared by every Python service."""
try:
    import os,re,requests
    from urllib.parse import urlparse
    import telegram_sources as ts

    # Amazon gelir ortaklığı etiketi hiçbir çalışma yolunda boş kalmasın.
    ts.AMAZON_TAG=(os.environ.get('AMAZON_ASSOCIATE_TAG') or 'ozelfirsat09-21').strip() or 'ozelfirsat09-21'

    _base_normalize=ts.normalize
    def normalize_ext(site,url):
        if site=='Amazon' and url:
            try:
                p=urlparse(ts.clean(url));m=re.search(r'/(?:dp|gp/product)/([A-Z0-9]{8,12})(?:[/?]|$)',p.path,re.I)
                if m:return f'https://www.amazon.com.tr/dp/{m.group(1).upper()}?tag={ts.AMAZON_TAG}'
            except Exception:pass
        return _base_normalize(site,url)
    ts.normalize=normalize_ext

    ts.MARKET['n11.com']='N11'
    ts.SHORT.update({'n11.com':'N11','www.n11.com':'N11'})

    _old_valid=ts.valid
    def valid_ext(site,url):
        if site!='N11':return _old_valid(site,url)
        p=urlparse(url or '');host=p.netloc.lower().replace('www.','')
        return host.endswith('n11.com') and bool(re.search(r'/urun/[^/?#]+',p.path,re.I))
    ts.valid=valid_ext

    _old_http=ts.search_marketplace_http
    def search_http_ext(site,title):
        if site!='N11':return _old_http(site,title)
        if not title:return None
        try:
            url='https://www.n11.com/arama?q='+requests.utils.quote(' '.join(title.split())[:140]);r=requests.get(url,headers=ts.HEAD,timeout=7)
            if r.status_code>=400:return None
            from bs4 import BeautifulSoup
            candidates=[]
            for a in BeautifulSoup(r.text,'html.parser').select('a[href]'):
                u=ts.clean(a.get('href') or '')
                if ts.valid('N11',u):candidates.append((ts.title_score(title,(a.get_text(' ',strip=True) or '')[:500]+' '+u),u))
            if candidates:
                score,u=max(candidates,key=lambda x:x[0])
                if score>=0.22:return ts.normalize('N11',u)
        except Exception:pass
        return None
    ts.search_marketplace_http=search_http_ext

    _old_browser=ts.search_marketplace_browser
    def search_browser_ext(page,site,title):
        if site!='N11':return _old_browser(page,site,title)
        try:
            url='https://www.n11.com/arama?q='+requests.utils.quote(' '.join((title or '').split())[:140]);page.goto(url,wait_until='domcontentloaded',timeout=9000);candidates=[]
            for a in page.locator('a[href]').all():
                u=ts.clean(a.get_attribute('href') or '')
                if ts.valid('N11',u):candidates.append((ts.title_score(title,(a.inner_text() or '')[:500]+' '+u),u))
            if candidates:
                score,u=max(candidates,key=lambda x:x[0])
                if score>=0.22:return ts.normalize('N11',u)
        except Exception:pass
        return None
    ts.search_marketplace_browser=search_browser_ext
except Exception as exc:
    print(f'Marketplace extension warning: {type(exc).__name__}: {exc}')