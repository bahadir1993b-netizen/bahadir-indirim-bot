import re, json, statistics, requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse

HEAD={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36','Accept-Language':'tr-TR,tr;q=0.9,en;q=0.8','Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8','Cache-Control':'no-cache','Pragma':'no-cache'}

def price(v):
    if v is None:return None
    s=re.sub(r'[^0-9,.]','',str(v)).strip()
    if not s:return None
    if ',' in s and '.' in s:s=s.replace('.','').replace(',','.') if s.rfind(',')>s.rfind('.') else s.replace(',','')
    elif ',' in s:
        a,b=s.rsplit(',',1);s=a.replace('.','')+'.'+b if len(b)<=2 else s.replace(',','')
    elif '.' in s:
        a,b=s.rsplit('.',1);s=s.replace('.','') if len(b)>2 else s
    try:
        x=float(s);return x if 1<x<10000000 else None
    except:return None

def _uniq(vals):
    out=[]
    for x in sorted(v for v in vals if v):
        if not out or abs(x-out[-1])/max(x,1)>.002:out.append(x)
    return out

def campaign_from_text(text,base_price=None):
    t=re.sub(r'\s+',' ',text or '');campaigns=[]
    for m in re.finditer(r'\b(\d+)\s*(?:adet\s*)?al\s*(\d+)\s*(?:adet\s*)?(?:öde|ode)\b',t,re.I):
        buy,paid=int(m.group(1)),int(m.group(2))
        if buy>paid>0:campaigns.append({'label':f'{buy} al {paid} öde','qty':buy,'effective':base_price*paid/buy if base_price else None,'priority':100})
    m=re.search(r'(\d+)\s*adet\s*(?:satın\s*al(?:ın|in)?|alin).*?(\d+)\s*(?:\.\s*)?adette?\s*(?:geçerli\s*)?%\s*(\d{1,2})\s*indirim',t,re.I)
    if m and base_price:
        buy,disc_qty,pct=map(int,m.groups())
        if buy>0 and 0<disc_qty<=buy and 0<pct<100:
            eff=base_price*((buy-disc_qty)+disc_qty*(1-pct/100))/buy
            campaigns.append({'label':f'{buy} adet alımda {disc_qty} üründe %{pct} indirim','qty':buy,'effective':eff,'priority':95})
    best=sorted(campaigns,key=lambda x:((x.get('effective') is not None),x.get('priority',0),-(x.get('effective') or 10**9)),reverse=True)[0] if campaigns else None
    return {'best':best,'all':campaigns,'effective':best.get('effective') if best else None,'qty':best.get('qty') if best else None}

def _decode_img(v):
    if not v:return ''
    if isinstance(v,str) and v.startswith('http'):return v.replace('\\u0026','&').replace('\\/','/')
    if isinstance(v,str):
        try:
            obj=json.loads(v)
            if isinstance(obj,dict) and obj:
                return max(obj.items(),key=lambda kv:(kv[1][0]*kv[1][1] if isinstance(kv[1],list) and len(kv[1])>1 else 0))[0]
        except:pass
    return ''

def _product_image(soup,raw=''):
    selectors=[('#landingImage','data-old-hires'),('#landingImage','data-a-dynamic-image'),('#landingImage','src'),('#imgBlkFront','data-a-dynamic-image'),('#imgBlkFront','src'),('img[data-old-hires]','data-old-hires'),('img[data-a-dynamic-image]','data-a-dynamic-image'),('meta[property="og:image"]','content'),('meta[name="twitter:image"]','content'),('img[itemprop="image"]','src')]
    for sel,attr in selectors:
        e=soup.select_one(sel)
        if e:
            u=_decode_img(e.get(attr) or '')
            if u.startswith('http'):return u
    # Amazon bazen görselleri sadece sayfa içi JS nesnesinde döndürüyor.
    pats=[r'"hiRes"\s*:\s*"(https:[^"\\]+(?:\\.[^"\\]*)*)"',r'"large"\s*:\s*"(https:[^"\\]+(?:\\.[^"\\]*)*)"',r'"mainUrl"\s*:\s*"(https:[^"\\]+(?:\\.[^"\\]*)*)"']
    for pat in pats:
        m=re.search(pat,raw or '',re.I)
        if m:
            u=m.group(1).replace('\\u0026','&').replace('\\/','/')
            if u.startswith('http'):return u
    return ''

def _title(soup,raw=''):
    for sel,attr in [('#productTitle',None),('meta[property="og:title"]','content'),('meta[name="title"]','content'),('h1',None),('title',None)]:
        e=soup.select_one(sel)
        if e:
            v=(e.get(attr) if attr else e.get_text(' ',strip=True)) or ''
            v=re.sub(r'\s+',' ',v).strip()
            if len(v)>4 and 'robot check' not in v.lower():return v[:300]
    for pat in [r'"title"\s*:\s*"([^"\\]{5,300})"',r'productTitle[^>]*>\s*([^<]{5,300})<']:
        m=re.search(pat,raw or '',re.I)
        if m:return re.sub(r'\\u([0-9a-fA-F]{4})',lambda x:chr(int(x.group(1),16)),m.group(1)).replace('\\/','/')[:300]
    return ''

def _amazon_asin(url):
    m=re.search(r'/(?:dp|gp/product)/([A-Z0-9]{8,12})',url or '',re.I)
    return m.group(1).upper() if m else None

def _fetch(url,timeout=8):
    try:return requests.get(url,headers=HEAD,timeout=timeout,allow_redirects=True)
    except:return None

def inspect_page(url,expected=None):
    out={'ok':False,'available':None,'live':None,'old':None,'title':'','image':'','campaign':None,'campaigns':[],'text':''}
    responses=[]
    r=_fetch(url,8)
    if r is not None and r.ok:responses.append(r)
    asin=_amazon_asin(url)
    # İlk Amazon isteği başlık/görsel vermediyse mobil ürün sayfası ikinci ve hızlı HTTP kaynağıdır.
    if asin and (not responses or len(responses[0].text)<5000):
        r2=_fetch(f'https://www.amazon.com.tr/gp/aw/d/{asin}?th=1&psc=1',7)
        if r2 is not None and r2.ok:responses.append(r2)
    if not responses:return out
    for resp in responses:
        raw=resp.text;soup=BeautifulSoup(raw,'html.parser');text=re.sub(r'\s+',' ',soup.get_text(' ',strip=True));low=text.lower();out['ok']=True
        if not out['text']:out['text']=text[:12000]
        bad=['stokta yok','stokta bulunmamaktadır','stokta bulunmuyor','ürün tükendi','currently unavailable','out of stock','sold out','satışa kapalı','şu anda mevcut değil']
        if any(x in low for x in bad):out['available']=False
        elif out['available'] is None and any(x in low for x in ['sepete ekle','hemen al','şimdi al','add to cart','buy now']):out['available']=True
        if not out['title']:out['title']=_title(soup,raw)
        if not out['image']:out['image']=_product_image(soup,raw)
        live=[];old=[]
        selectors=[('meta[property="product:price:amount"]','content'),('meta[itemprop="price"]','content'),('[itemprop="price"]','content'),('.a-price .a-offscreen',None),('.apexPriceToPay .a-offscreen',None),('[data-test-id="price-current-price"]',None),('[class*="currentPrice"]',None),('[class*="salePrice"]',None),('.prc-dsc',None),('.prc-slg',None)]
        for sel,attr in selectors:
            for e in soup.select(sel)[:12]:
                p=price(e.get(attr) if attr else e.get_text(' ',strip=True))
                if p and (not expected or expected*.45<=p<=expected*1.8):live.append(p)
        for sel in ['del','s','.old-price','.list-price','[class*="oldPrice"]','[class*="listPrice"]','.a-text-price .a-offscreen','.basisPrice .a-offscreen']:
            for e in soup.select(sel)[:12]:
                p=price(e.get('content') or e.get('data-price') or e.get_text(' ',strip=True))
                if p:old.append(p)
        live=_uniq(live);old=_uniq(old)
        if live:
            cand=[p for p in live if not expected or expected*.60<=p<=expected*1.50]
            val=min(cand,key=lambda p:abs(p-(expected or p))) if cand else min(live)
            if not out['live']:out['live']=val
        if out['live']:
            candidates=[p for p in old if out['live']*1.03<p<=out['live']*1.8]
            if candidates:out['old']=min(candidates)
        if not out['campaign']:
            camp=campaign_from_text(text,out['live']);out['campaign']=camp.get('best');out['campaigns']=camp.get('all') or []
    # Amazon ikinci isteği, ilk istek 200 olsa bile başlık/görsel eksikse de denenir.
    if asin and (not out['title'] or not out['image']):
        r3=_fetch(f'https://www.amazon.com.tr/gp/aw/d/{asin}?th=1&psc=1&ref_=navm_hdr_signin',7)
        if r3 is not None and r3.ok:
            soup=BeautifulSoup(r3.text,'html.parser')
            if not out['title']:out['title']=_title(soup,r3.text)
            if not out['image']:out['image']=_product_image(soup,r3.text)
    return out

def choose_reference(current,history=None,source=None,page=None,market_median=None,market_floor=None):
    raw_history=[float(x) for x in (history or []) if x and current*.70<=float(x)<=current*1.80]
    stable=[x for x in raw_history if abs(x-current)/max(current,1)<=0.05]
    if len(stable)>=3 and len(stable)>=max(3,int(len(raw_history)*0.60)):return None,'stable-price-history'
    high_history=[x for x in raw_history if current*1.03<x<=current*1.8];hist=min(high_history) if high_history else None
    local=[float(x) for x in (hist,source,page) if x and current*1.03<float(x)<=current*1.8];local_ref=min(local) if local else None
    if market_floor and current>market_floor*1.05:return None,'market-blocked'
    if local_ref:
        if market_median and market_median>current*1.03:return min(local_ref,float(market_median)),'market+history'
        if local_ref>current*1.45:return None,'unverified-high-reference'
        return local_ref,'history/page-conservative'
    if market_floor and market_median and market_floor>current*1.10 and market_median<=market_floor*1.45:return float(market_floor),'market-floor'
    return None,'no-historical-reference'
