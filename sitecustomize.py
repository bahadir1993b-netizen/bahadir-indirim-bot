"""Runtime marketplace and publishing safety extensions shared by every Python service."""
try:
    import os,re,json,requests
    from datetime import datetime,timezone,timedelta
    from urllib.parse import urlparse,urlsplit,urlunsplit,parse_qsl,urlencode
    import telegram_sources as ts
    import local_store as ls

    ts.AMAZON_TAG=(os.environ.get('AMAZON_ASSOCIATE_TAG') or os.environ.get('AMAZON_TAG') or 'ozelfirsat09-21').strip() or 'ozelfirsat09-21'

    _base_normalize=ts.normalize
    def normalize_ext(site,url):
        if site=='Amazon' and url:
            try:
                p=urlparse(ts.clean(url));m=re.search(r'/(?:dp|gp/product)/([A-Z0-9]{8,12})(?:[/?]|$)',p.path,re.I)
                if m:return f'https://www.amazon.com.tr/dp/{m.group(1).upper()}?tag={ts.AMAZON_TAG}'
            except Exception:pass
        return _base_normalize(site,url)
    ts.normalize=normalize_ext

    # SON YAYIN KAPISI: hangi servis gönderirse göndersin Telegram'a çıkan bütün
    # amazon.com.tr bağlantılarında affiliate tag zorunlu olsun.
    def _affiliate_url(url):
        if not isinstance(url,str) or 'amazon.com.tr' not in url.lower():return url
        try:
            p=urlsplit(url)
            if not p.netloc.lower().endswith('amazon.com.tr'):return url
            q=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if k.lower()!='tag']
            q.append(('tag',ts.AMAZON_TAG))
            return urlunsplit((p.scheme,p.netloc,p.path,urlencode(q,doseq=True),p.fragment))
        except Exception:
            return url+('&' if '?' in url else '?')+'tag='+ts.AMAZON_TAG

    def _walk_affiliate(obj):
        if isinstance(obj,dict):
            out={}
            for k,v in obj.items():
                out[k]=_affiliate_url(v) if k=='url' and isinstance(v,str) else _walk_affiliate(v)
            return out
        if isinstance(obj,list):return [_walk_affiliate(x) for x in obj]
        if isinstance(obj,str):
            return re.sub(r'https?://(?:www\.)?amazon\.com\.tr/[^\s<>"\']+',lambda m:_affiliate_url(m.group(0)),obj,flags=re.I)
        return obj

    _requests_post=requests.post
    def guarded_post(url,*args,**kwargs):
        if isinstance(url,str) and 'api.telegram.org/' in url:
            if isinstance(kwargs.get('json'),dict):kwargs['json']=_walk_affiliate(kwargs['json'])
            if isinstance(kwargs.get('data'),dict):
                data=dict(kwargs['data'])
                rm=data.get('reply_markup')
                if isinstance(rm,str):
                    try:data['reply_markup']=json.dumps(_walk_affiliate(json.loads(rm)),ensure_ascii=False)
                    except Exception:pass
                elif isinstance(rm,(dict,list)):data['reply_markup']=_walk_affiliate(rm)
                for k in ('text','caption'):
                    if isinstance(data.get(k),str):data[k]=_walk_affiliate(data[k])
                kwargs['data']=data
        return _requests_post(url,*args,**kwargs)
    requests.post=guarded_post

    # Direct/web/fast yayın kapıları yerel publish_log kullanıyor. Eski Telegram
    # yolu yalnız Supabase'e yazmışsa da aynı ürünü tekrar basmamak için fallback.
    _local_recent=ls.recently_published
    def recent_ext(url,price,days=30,min_drop=.05):
        if _local_recent(url,price,days,min_drop):return True
        key=ls.canonical(url)
        if not key or not price:return False
        try:
            since=(datetime.now(timezone.utc)-timedelta(days=int(days))).isoformat()
            rows=ts.sb('GET','price_history',params={'select':'price,product_url,recorded_at','recorded_at':f'gte.{since}','order':'recorded_at.desc','limit':'500'})
            for r in rows:
                ru=r.get('product_url') or ''
                if ru.startswith('telegram://') or ls.canonical(ru)!=key:continue
                old=float(r.get('price') or 0)
                if old and float(price)>=old*(1-float(min_drop)):return True
        except Exception:pass
        return False
    ls.recently_published=recent_ext

    ts.MARKET['n11.com']='N11';ts.SHORT.update({'n11.com':'N11','www.n11.com':'N11'})
    _old_valid=ts.valid
    def valid_ext(site,url):
        if site!='N11':return _old_valid(site,url)
        p=urlparse(url or '');host=p.netloc.lower().replace('www.','');return host.endswith('n11.com') and bool(re.search(r'/urun/[^/?#]+',p.path,re.I))
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
