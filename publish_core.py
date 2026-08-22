import os,re,json,html,requests
from urllib.parse import urlparse,urlsplit,urlunsplit,parse_qsl,urlencode
from bs4 import BeautifulSoup

AMAZON_TAG=(os.environ.get('AMAZON_ASSOCIATE_TAG') or os.environ.get('AMAZON_TAG') or 'ozelfirsat09-21').strip() or 'ozelfirsat09-21'
HEAD={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36','Accept-Language':'tr-TR,tr;q=0.9,en;q=0.8'}
BAD={'ürün','fırsat ürünü','firsat urunu','sepete ekleniyor','sepete ekle','hemen al','amazon','amazon.com.tr'}

def clean_title(value):
    s=html.unescape(str(value or '')).replace('\n',' ')
    s=re.sub(r'(?i)^\s*(?:ÖZEL\s+FIRSATLAR\s*-\s*Güncel\s+İndirimler|Amazon\s+İndirimleri\s*-\s*Özel\s+Fırsatlar|Fırsat\s+Merkezi)\s*',' ',s)
    s=re.sub(r'@[A-Za-z0-9_]+|#(?:tanıtım|tanitim|reklam)',' ',s,flags=re.I)
    s=re.sub(r'(?i)\b(?:sohbet grubumuz|kanalımıza katıl|takip et)\b.*$',' ',s)
    s=re.sub(r'\s*[:|\-]?\s*Amazon\.com\.tr\s*:\s*.*$',' ',s,flags=re.I)
    s=re.sub(r'(?i)^\s*(?:sepete ekleniyor|sepete ekle|hemen al)\s*[.!…-]*\s*',' ',s)
    s=re.sub(r'\s+',' ',s).strip(' -|•:')
    return s[:200] if len(s)>=5 and s.lower() not in BAD else ''

def generic_title(value):return not bool(clean_title(value))

def affiliate_url(url):
    if not isinstance(url,str) or 'amazon.com.tr' not in url.lower():return url
    p=urlsplit(url);q=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if k.lower()!='tag'];q.append(('tag',AMAZON_TAG))
    return urlunsplit((p.scheme,p.netloc,p.path,urlencode(q,doseq=True),p.fragment))

def affiliate_ok(url):
    if 'amazon.com.tr' not in (url or '').lower():return True
    try:return any(k.lower()=='tag' and v==AMAZON_TAG for k,v in parse_qsl(urlsplit(url).query,keep_blank_values=True))
    except:return False

def product_identity(url):
    try:
        p=urlparse(url or '');h=p.netloc.lower().replace('www.','');path=p.path
        if h.endswith('amazon.com.tr'):
            m=re.search(r'/(?:dp|gp/product)/([A-Z0-9]{8,12})(?:[/?]|$)',path,re.I)
            if m:return 'amazon:'+m.group(1).upper()
        if h.endswith('hepsiburada.com'):
            m=re.search(r'-p-([A-Za-z0-9]+)(?:[/?]|$)',path,re.I)
            if m:return 'hepsiburada:'+m.group(1).lower()
        if h.endswith('trendyol.com'):
            m=re.search(r'-p-(\d+)(?:[/?]|$)',path,re.I)
            if m:return 'trendyol:'+m.group(1)
        if h.endswith('n11.com'):
            m=re.search(r'/urun/([^/?#]+)',path,re.I)
            if m:return 'n11:'+m.group(1).lower()
        return 'url:'+h+path.rstrip('/').lower()
    except:return 'url:'+str(url or '').lower()

def _tokens(text):
    stop={'ürün','ürünü','fırsat','indirim','adet','parça','set','marka','model','yeni','tl','sadece','stok','kampanya','sepette'}
    return {x for x in re.findall(r'[a-zçğıöşü0-9]{3,}',(text or '').lower()) if x not in stop}

def _score(a,b):
    x,y=_tokens(a),_tokens(b);return len(x&y)/max(1,len(x)) if x and y else 0

def _jsonld(soup):
    queue=[]
    for el in soup.select('script[type="application/ld+json"]'):
        try:data=json.loads(el.string or el.get_text() or '{}');queue.extend(data if isinstance(data,list) else [data])
        except:continue
    for obj in queue:
        if not isinstance(obj,dict):continue
        graph=obj.get('@graph')
        if isinstance(graph,list):queue.extend(x for x in graph if isinstance(x,dict))
        title=clean_title(obj.get('name') or '');image=obj.get('image');image=image[0] if isinstance(image,list) and image else image
        if isinstance(image,dict):image=image.get('url')
        if title or (isinstance(image,str) and image.startswith('http')):return title,image
    return '',None

def _search_image(site,title):
    base={'Amazon':'https://www.amazon.com.tr/s?k=','Hepsiburada':'https://www.hepsiburada.com/ara?q=','Trendyol':'https://www.trendyol.com/sr?q=','N11':'https://www.n11.com/arama?q='}.get(site)
    if not base or not title:return None
    try:
        r=requests.get(base+requests.utils.quote(title[:140]),headers=HEAD,timeout=6,allow_redirects=True)
        if not r.ok:return None
        soup=BeautifulSoup(r.text,'html.parser');best=(0,None)
        for card in soup.select('div[data-asin],li,article,div[class*="product"],div[class*="p-card"]')[:100]:
            score=_score(title,card.get_text(' ',strip=True));img=card.select_one('img[src],img[data-src]');src=(img.get('src') or img.get('data-src')) if img else None
            if src and src.startswith('http') and score>best[0]:best=(score,src)
        return best[1] if best[0]>=.50 else None
    except:return None

def resolve_meta(url,site='',hint='',timeout=7):
    urls=[url]
    if site=='Amazon' or 'amazon.com.tr' in (url or '').lower():
        m=re.search(r'/(?:dp|gp/product)/([A-Z0-9]{8,12})',url or '',re.I)
        if m:
            a=m.group(1).upper();urls += [f'https://www.amazon.com.tr/dp/{a}?th=1&psc=1',f'https://www.amazon.com.tr/gp/aw/d/{a}']
    best_title=clean_title(hint);best_image=None;resolved=url
    for u in urls:
        try:r=requests.get(u,headers=HEAD,timeout=timeout,allow_redirects=True)
        except:continue
        if not r.ok:continue
        resolved=r.url or resolved;soup=BeautifulSoup(r.text or '','html.parser');title='';image=None
        for sel,attr in [('#productTitle',None),('h1',None),('meta[property="og:title"]','content'),('meta[name="twitter:title"]','content')]:
            e=soup.select_one(sel)
            if e:
                title=clean_title(e.get(attr) if attr else e.get_text(' ',strip=True))
                if title:break
        for sel,attr in [('#landingImage','data-old-hires'),('#landingImage','src'),('img#imgBlkFront','src'),('meta[property="og:image"]','content'),('meta[name="twitter:image"]','content'),('[itemprop="image"]','content'),('[itemprop="image"]','src')]:
            e=soup.select_one(sel)
            if e:
                v=e.get(attr) or ''
                if isinstance(v,str) and v.startswith('http'):image=v;break
        if not image:
            e=soup.select_one('img.a-dynamic-image')
            if e:
                try:
                    d=json.loads(e.get('data-a-dynamic-image') or '{}')
                    if d:image=next(iter(d.keys()))
                except:pass
        if not title or not image:
            jt,ji=_jsonld(soup);title=title or jt;image=image or ji
        if title:best_title=title
        if image:best_image=image
        if best_title and best_image:break
    if best_title and not best_image:best_image=_search_image(site,best_title)
    return {'title':best_title,'image':best_image,'resolved_url':resolved}
