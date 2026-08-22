"""Runtime marketplace and publishing safety extensions shared by every Python service."""
try:
    import os,re,json,requests
    from datetime import datetime,timezone,timedelta
    from urllib.parse import urlparse,urljoin
    import telegram_sources as ts
    import local_store as ls
    import publish_core as pc

    ts.AMAZON_TAG=(os.environ.get('AMAZON_ASSOCIATE_TAG') or os.environ.get('AMAZON_TAG') or 'ozelfirsat09-21').strip() or 'ozelfirsat09-21'

    _base_normalize=ts.normalize
    def normalize_ext(site,url):
        if site=='Amazon' and url:
            try:
                p=urlparse(ts.clean(url));m=re.search(r'/(?:dp|gp/product)/([A-Z0-9]{8,12})(?:[/?]|$)',p.path,re.I)
                if m:return pc.affiliate_url(f'https://www.amazon.com.tr/dp/{m.group(1).upper()}')
            except Exception:pass
        return _base_normalize(site,url)
    ts.normalize=normalize_ext

    def _walk_affiliate(obj):
        if isinstance(obj,dict):return {k:(pc.affiliate_url(v) if k=='url' and isinstance(v,str) else _walk_affiliate(v)) for k,v in obj.items()}
        if isinstance(obj,list):return [_walk_affiliate(x) for x in obj]
        if isinstance(obj,str):return re.sub(r'https?://(?:www\.)?amazon\.com\.tr/[^\s<>"\']+',lambda m:pc.affiliate_url(m.group(0)),obj,flags=re.I)
        return obj

    def _button_url(markup):
        try:
            if isinstance(markup,str):markup=json.loads(markup)
            for row in (markup or {}).get('inline_keyboard',[]):
                for b in row:
                    if isinstance(b,dict) and isinstance(b.get('url'),str):return b['url']
        except:pass
        return None

    def _repair_generic(text,markup):
        if not isinstance(text,str):return text
        if not re.search(r'(?im)^\s*🛍️\s*(?:Fırsat Ürünü|Firsat Urunu|Ürün)\s*$',text):return text
        u=_button_url(markup)
        if not u:raise RuntimeError('publish_integrity: generic title and no product URL')
        site=ts.site(u) or ('Amazon' if 'amazon.com.tr' in u else 'Hepsiburada' if 'hepsiburada.com' in u else 'Trendyol' if 'trendyol.com' in u else 'N11' if 'n11.com' in u else '')
        meta=pc.resolve_meta(u,site,'');title=pc.clean_title(meta.get('title'))
        if not title:raise RuntimeError('publish_integrity: product title could not be resolved')
        return re.sub(r'(?im)^(\s*🛍️\s*)(?:Fırsat Ürünü|Firsat Urunu|Ürün)\s*$',lambda m:m.group(1)+title,text)

    _requests_post=requests.post
    def guarded_post(url,*args,**kwargs):
        if isinstance(url,str) and 'api.telegram.org/' in url:
            if isinstance(kwargs.get('json'),dict):
                data=_walk_affiliate(dict(kwargs['json']));rm=data.get('reply_markup')
                for k in ('text','caption'):
                    if isinstance(data.get(k),str):data[k]=_repair_generic(data[k],rm)
                kwargs['json']=data
            if isinstance(kwargs.get('data'),dict):
                data=dict(kwargs['data']);rm=data.get('reply_markup');parsed_rm=rm
                if isinstance(rm,str):
                    try:parsed_rm=_walk_affiliate(json.loads(rm));data['reply_markup']=json.dumps(parsed_rm,ensure_ascii=False)
                    except Exception:parsed_rm=rm
                elif isinstance(rm,(dict,list)):parsed_rm=_walk_affiliate(rm);data['reply_markup']=parsed_rm
                for k in ('text','caption'):
                    if isinstance(data.get(k),str):data[k]=_repair_generic(_walk_affiliate(data[k]),parsed_rm)
                kwargs['data']=data
        return _requests_post(url,*args,**kwargs)
    requests.post=guarded_post

    _local_recent=ls.recently_published
    def recent_ext(url,price,days=30,min_drop=.05):
        if _local_recent(url,price,days,min_drop):return True
        key=ls.publication_key(url)
        if not key or not price:return False
        try:
            since=(datetime.now(timezone.utc)-timedelta(days=int(days))).isoformat();rows=ts.sb('GET','price_history',params={'select':'price,product_url,recorded_at','recorded_at':f'gte.{since}','order':'recorded_at.desc','limit':'500'})
            for r in rows:
                ru=r.get('product_url') or ''
                if ru.startswith('telegram://') or ls.publication_key(ru)!=key:continue
                old=float(r.get('price') or 0)
                if old and float(price)>=old*(1-float(min_drop)):return True
        except Exception as e:print(f'DUPLICATE FALLBACK UYARI | {type(e).__name__}')
        return False
    ls.recently_published=recent_ext

    ts.MARKET['n11.com']='N11';ts.SHORT.update({'n11.com':'N11','www.n11.com':'N11'})
    _old_valid=ts.valid
    def valid_ext(site,url):
        if site!='N11':return _old_valid(site,url)
        p=urlparse(url or '');host=p.netloc.lower().replace('www.','');return host.endswith('n11.com') and bool(re.search(r'/urun/[^/?#]+',p.path,re.I))
    ts.valid=valid_ext

    SEARCH_BASE={'Amazon':'https://www.amazon.com.tr/s?k=','Hepsiburada':'https://www.hepsiburada.com/ara?q=','Trendyol':'https://www.trendyol.com/sr?q=','N11':'https://www.n11.com/arama?q='}
    def _strict_http(site,title):
        base=SEARCH_BASE.get(site)
        if not base or not title:return None
        try:
            from bs4 import BeautifulSoup
            r=requests.get(base+requests.utils.quote(' '.join(title.split())[:140]),headers=ts.HEAD,timeout=7)
            if r.status_code>=400:return None
            candidates=[]
            for a in BeautifulSoup(r.text,'html.parser').select('a[href]'):
                u=urljoin(r.url,a.get('href') or '')
                if ts.valid(site,u):candidates.append((ts.title_score(title,(a.get_text(' ',strip=True) or '')[:500]+' '+u),u))
            if candidates:
                score,u=max(candidates,key=lambda x:x[0])
                if score>=0.50:return ts.normalize(site,u)
        except Exception as e:print(f'{site} SEARCH UYARI | {type(e).__name__}')
        return None
    ts.search_marketplace_http=_strict_http

    def _strict_browser(page,site,title):
        base=SEARCH_BASE.get(site)
        if not base or not title:return None
        try:
            page.goto(base+requests.utils.quote(' '.join(title.split())[:140]),wait_until='domcontentloaded',timeout=9000);candidates=[]
            for a in page.locator('a[href]').all():
                u=urljoin(page.url,a.get_attribute('href') or '')
                if ts.valid(site,u):candidates.append((ts.title_score(title,(a.inner_text() or '')[:500]+' '+u),u))
            if candidates:
                score,u=max(candidates,key=lambda x:x[0])
                if score>=0.50:return ts.normalize(site,u)
        except Exception as e:print(f'{site} BROWSER SEARCH UYARI | {type(e).__name__}')
        return None
    ts.search_marketplace_browser=_strict_browser

    _base_http_resolve=ts.http_resolve
    def _extract_market_url_from_html(base_url,html,site):
        if not html:return None
        from bs4 import BeautifulSoup
        soup=BeautifulSoup(html,'html.parser')
        candidates=[]
        for sel,attr in [('link[rel="canonical"]','href'),('meta[property="og:url"]','content'),('meta[name="twitter:url"]','content')]:
            e=soup.select_one(sel)
            if e:candidates.append(urljoin(base_url,e.get(attr) or ''))
        for e in soup.select('meta[http-equiv="refresh"]'):
            m=re.search(r'url\s*=\s*([^;]+)$',e.get('content') or '',re.I)
            if m:candidates.append(urljoin(base_url,m.group(1).strip(' \"\'')))
        for a in soup.select('a[href]'):
            candidates.append(urljoin(base_url,a.get('href') or ''))
        for m in re.finditer(r'https?://[^\s"\'<>]+',html):
            candidates.append(m.group(0).replace('\\/','/'))
        for u in candidates:
            try:
                if ts.site(u)==site and ts.valid(site,u):return ts.normalize(site,u)
            except Exception:continue
        return None

    def _short_browser_resolve(page,url,site):
        if not url:return None
        try:
            page.goto(url,wait_until='domcontentloaded',timeout=12000)
            final=ts.clean(page.url)
            if ts.site(final)==site and ts.valid(site,final):
                print(f'SHORT LINK BROWSER OK | {site} | {urlparse(url).netloc}')
                return ts.normalize(site,final)
            html=page.content()
            found=_extract_market_url_from_html(final or url,html,site)
            if found:
                print(f'SHORT LINK HTML OK | {site} | {urlparse(url).netloc}')
                return found
        except Exception as e:print(f'SHORT LINK BROWSER UYARI | {site} | {type(e).__name__}')
        return None

    def _resolve_ext(page,url,site,title):
        u=_base_http_resolve(url,site)
        if u:return u
        host=urlparse(url or '').netloc.lower().replace('www.','')
        if host in ts.SHORT or any(host.endswith('.'+x) for x in ts.SHORT):
            u=_short_browser_resolve(page,url,site)
            if u:return u
        u=_strict_http(site,title)
        if u:return u
        return _strict_browser(page,site,title)
    ts.resolve=_resolve_ext
except Exception as exc:
    print(f'Marketplace extension warning: {type(exc).__name__}: {exc}')
