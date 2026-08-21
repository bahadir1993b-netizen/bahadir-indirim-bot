import re, json, statistics, requests
from bs4 import BeautifulSoup

HEAD={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36','Accept-Language':'tr-TR,tr;q=0.9,en;q=0.8'}

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
    t=re.sub(r'\s+',' ',text or '')
    campaigns=[];effective=None;qty=None
    for m in re.finditer(r'\b(\d+)\s*al\s*(\d+)\s*(?:öde|ode)\b',t,re.I):
        buy,paid=int(m.group(1)),int(m.group(2))
        if buy>paid>0:
            eff=base_price*paid/buy if base_price else None
            campaigns.append({'label':f'{buy} al {paid} öde','qty':buy,'effective':eff,'priority':100})
    m=re.search(r'(\d+)\s*adet\s*(?:satın alın|satin alin).*?(\d+)\s*adette?\s*geçerli\s*%\s*(\d{1,2})\s*indirim',t,re.I)
    if m and base_price:
        buy,disc_qty,pct=map(int,m.groups())
        if buy>0 and 0<disc_qty<=buy and 0<pct<100:
            total=base_price*(buy-disc_qty)+base_price*disc_qty*(1-pct/100)
            eff=total/buy
            campaigns.append({'label':f'{buy} adet alımda {disc_qty} üründe %{pct} indirim','qty':buy,'effective':eff,'priority':90})
    for m in re.finditer(r'(\d[\d.,]*)\s*(?:TL|₺).*?(\d[\d.,]*)\s*(?:TL|₺)\s*(?:tasarruf|indirim)',t,re.I):
        threshold,saving=price(m.group(1)),price(m.group(2))
        if threshold and saving and threshold>saving:
            campaigns.append({'label':f'{threshold:.0f} TL seçili ürün alışverişine {saving:.0f} TL indirim','qty':None,'effective':None,'priority':20})
    if campaigns:
        best=sorted(campaigns,key=lambda x:((x.get('effective') is not None),x.get('priority',0),-(x.get('effective') or 10**9)),reverse=True)[0]
        effective=best.get('effective');qty=best.get('qty')
    else:best=None
    return {'best':best,'all':campaigns,'effective':effective,'qty':qty}

def inspect_page(url,expected=None):
    out={'ok':False,'available':None,'live':None,'old':None,'title':'','image':'','campaign':None,'campaigns':[],'text':''}
    try:
        r=requests.get(url,headers=HEAD,timeout=12,allow_redirects=True)
        if not r.ok:return out
        out['ok']=True;soup=BeautifulSoup(r.text,'html.parser');text=re.sub(r'\s+',' ',soup.get_text(' ',strip=True));low=text.lower();out['text']=text[:12000]
        bad=['stokta yok','stokta bulunmamaktadır','stokta bulunmuyor','ürün tükendi','urun tukendi','tükendi','tukendi','currently unavailable','out of stock','sold out','satışa kapalı','satisa kapali']
        if any(x in low for x in bad):out['available']=False
        for sc in soup.select('script[type="application/ld+json"]'):
            try:
                obj=json.loads(sc.string or sc.get_text() or '{}');stack=obj if isinstance(obj,list) else [obj]
                for z in stack:
                    if not isinstance(z,dict):continue
                    av=str((z.get('offers') or {}).get('availability','')).lower() if isinstance(z.get('offers'),dict) else ''
                    if 'outofstock' in av:out['available']=False
                    elif 'instock' in av and out['available'] is None:out['available']=True
            except Exception:pass
        good=['sepete ekle','hemen al','şimdi al','simdi al','add to cart','buy now']
        if out['available'] is None and any(x in low for x in good):out['available']=True
        for sel,attr in [('meta[property="og:title"]','content'),('h1',None),('title',None)]:
            e=soup.select_one(sel)
            if e:
                out['title']=re.sub(r'\s+',' ',(e.get(attr) if attr else e.get_text(' ',strip=True)) or '').strip()[:300]
                if out['title']:break
        for sel,attr in [('meta[property="og:image"]','content'),('meta[name="twitter:image"]','content'),('img[itemprop="image"]','src')]:
            e=soup.select_one(sel)
            if e and (e.get(attr) or '').startswith('http'):out['image']=e.get(attr);break
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
            if expected:
                credible=[p for p in live if expected*.60<=p<=expected*1.50]
                out['live']=min(credible,key=lambda p:abs(p-expected)) if credible else min(live)
            else:out['live']=min(live)
        if out['live']:
            candidates=[p for p in old if out['live']*1.03<p<=out['live']*1.8]
            if candidates:out['old']=float(statistics.median(candidates))
        camp=campaign_from_text(text,out['live'])
        out['campaign']=camp.get('best');out['campaigns']=camp.get('all') or []
        return out
    except Exception:return out

def choose_reference(current,history=None,source=None,page=None,market_median=None,market_floor=None):
    """Conservative reference.

    A current-market median is useful for sanity checking, but it is not proof of a
    historical/previous price. A discount reference must come from the product's own
    history, a credible source old/list price, or the live product page. Market data
    may only corroborate/cap that local reference.
    """
    history=[float(x) for x in (history or []) if x and current*1.03<float(x)<=current*1.8]
    hist=float(statistics.median(history)) if history else None
    local=[x for x in (hist,source,page) if x and current*1.03<x<=current*1.8]
    local_ref=float(statistics.median(local)) if local else None

    # If the same product is already materially cheaper elsewhere, this is not a deal.
    if market_floor and current>market_floor*1.05:return None,'market-blocked'

    if local_ref:
        # Market median can only cap/corroborate a real historical/list-price signal.
        if market_median and market_median>current*1.03:
            ref=min(local_ref,market_median)
            return ref,'market+history'
        # Without external market corroboration, reject spectacular references.
        if local_ref>current*1.45:return None,'unverified-high-reference'
        return local_ref,'history/page'

    # Never manufacture an "old price" from today's cross-store median alone.
    return None,'no-historical-reference'
