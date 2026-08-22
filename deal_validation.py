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
    campaigns=[]
    for m in re.finditer(r'\b(\d+)\s*(?:adet\s*)?al\s*(\d+)\s*(?:adet\s*)?(?:öde|ode)\b',t,re.I):
        buy,paid=int(m.group(1)),int(m.group(2))
        if buy>paid>0:
            eff=base_price*paid/buy if base_price else None
            campaigns.append({'label':f'{buy} al {paid} öde','qty':buy,'effective':eff,'priority':100})
    patterns=[
        r'(\d+)\s*adet\s*(?:satın\s*al(?:ın|in)?|alin).*?(\d+)\s*(?:\.\s*)?adette?\s*(?:geçerli\s*)?%\s*(\d{1,2})\s*indirim',
        r'(\d+)\s*(?:\.\s*)?(?:üründe|urunde|ürüne|urune)\s*%\s*(\d{1,2})\s*indirim',
        r'(\d+)\s*(?:\.\s*)?(?:ürün|urun)\s*%\s*(\d{1,2})\s*(?:indirimli|indirim)'
    ]
    m=re.search(patterns[0],t,re.I)
    if m and base_price:
        buy,disc_qty,pct=map(int,m.groups())
        if buy>0 and 0<disc_qty<=buy and 0<pct<100:
            eff=base_price*((buy-disc_qty)+disc_qty*(1-pct/100))/buy
            campaigns.append({'label':f'{buy} adet alımda {disc_qty} üründe %{pct} indirim','qty':buy,'effective':eff,'priority':95})
    for pat in patterns[1:]:
        for m in re.finditer(pat,t,re.I):
            nth,pct=map(int,m.groups())
            if base_price and nth>=2 and 0<pct<100:
                eff=base_price*((nth-1)+(1-pct/100))/nth
                campaigns.append({'label':f'{nth}. üründe %{pct} indirim','qty':nth,'effective':eff,'priority':94})
    for m in re.finditer(r'(\d[\d.,]*)\s*(?:TL|₺).*?(\d[\d.,]*)\s*(?:TL|₺)\s*(?:tasarruf|indirim)',t,re.I):
        threshold,saving=price(m.group(1)),price(m.group(2))
        if threshold and saving and threshold>saving:
            campaigns.append({'label':f'{threshold:.0f} TL seçili ürün alışverişine {saving:.0f} TL indirim','qty':None,'effective':None,'priority':20})
    best=sorted(campaigns,key=lambda x:((x.get('effective') is not None),x.get('priority',0),-(x.get('effective') or 10**9)),reverse=True)[0] if campaigns else None
    return {'best':best,'all':campaigns,'effective':best.get('effective') if best else None,'qty':best.get('qty') if best else None}

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
            if candidates:out['old']=min(candidates)
        camp=campaign_from_text(text,out['live'])
        out['campaign']=camp.get('best');out['campaigns']=camp.get('all') or []
        return out
    except Exception:return out

def choose_reference(current,history=None,source=None,page=None,market_median=None,market_floor=None):
    raw_history=[float(x) for x in (history or []) if x and current*.70<=float(x)<=current*1.80]
    stable=[x for x in raw_history if abs(x-current)/max(current,1)<=0.05]
    # If we have several observations clustered around today's price, this is not a fresh deal.
    # Do not let an inflated list/source price manufacture a large discount.
    if len(stable)>=3 and len(stable)>=max(3,int(len(raw_history)*0.60)):
        if not page or page<=current*1.10:
            return None,'stable-price-history'

    high_history=[x for x in raw_history if current*1.03<x<=current*1.8]
    hist=min(high_history) if high_history else None
    local=[float(x) for x in (hist,source,page) if x and current*1.03<float(x)<=current*1.8]
    # Conservative rule: use the LOWEST credible reference, never the median/highest.
    # Example: page says 15,299 while a source claims 21,999 -> 15,299 wins.
    local_ref=min(local) if local else None

    if market_floor and current>market_floor*1.05:return None,'market-blocked'
    if local_ref:
        if market_median and market_median>current*1.03:return min(local_ref,float(market_median)),'market+history'
        if local_ref>current*1.45:return None,'unverified-high-reference'
        return local_ref,'history/page-conservative'
    if market_floor and market_median and market_floor>current*1.10 and market_median<=market_floor*1.45:
        return float(market_floor),'market-floor'
    return None,'no-historical-reference'
