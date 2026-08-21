import os,re,time,json,hashlib,statistics,requests
from pathlib import Path
from urllib.parse import urlparse

SERPER_API_KEY=(os.environ.get('SERPER_API_KEY') or '').strip()
CACHE_FILE=Path('/app/data/market_reference_cache.json')
CACHE_FILE.parent.mkdir(parents=True,exist_ok=True)
try:
    CACHE=json.loads(CACHE_FILE.read_text('utf-8')) if CACHE_FILE.exists() else {}
except Exception:
    CACHE={}

STOP={'urun','ürün','adet','paket','set','yeni','model','siyah','beyaz','icin','için','ile','ve','the','firsat','fırsat','indirim','kampanya','sepette','gecerli','geçerli'}

def _tokens(text):
    return {x for x in re.findall(r'[a-zçğıöşü0-9]{3,}',(text or '').lower()) if x not in STOP}

def _score(a,b):
    aa,bb=_tokens(a),_tokens(b)
    if not aa or not bb:return 0
    return len(aa&bb)/max(1,min(len(aa),len(bb)))

def _price(v):
    if v is None:return None
    s=re.sub(r'[^0-9,.]','',str(v))
    if not s:return None
    if ',' in s and '.' in s:
        s=s.replace('.','').replace(',','.') if s.rfind(',')>s.rfind('.') else s.replace(',','')
    elif ',' in s:
        a,b=s.rsplit(',',1);s=a.replace('.','')+'.'+b if len(b)<=2 else s.replace(',','')
    elif '.' in s:
        a,b=s.rsplit('.',1);s=s.replace('.','') if len(b)>2 else s
    try:
        x=float(s);return x if 1<x<10000000 else None
    except:return None

def _key(site,title):
    return hashlib.sha1(f'{site}|{re.sub(r"\s+"," ",(title or "").lower()).strip()}'.encode()).hexdigest()

def _save():
    try:CACHE_FILE.write_text(json.dumps(CACHE,ensure_ascii=False),'utf-8')
    except Exception:pass

def market_reference(site,title,current):
    """Return a conservative market reference price from Serper Shopping.
    It only accepts close title matches and returns the median of matching prices
    materially above current. Cached for 6 hours to protect query credits.
    """
    if not SERPER_API_KEY or not title or not current:return None,'none'
    k=_key(site,title);x=CACHE.get(k)
    if isinstance(x,dict) and time.time()-float(x.get('ts') or 0)<21600:
        val=_price(x.get('ref'));return val,('cache' if val else 'cache-none')
    try:
        q=re.sub(r'\s+',' ',title).strip()[:160]
        r=requests.post('https://google.serper.dev/shopping',headers={'X-API-KEY':SERPER_API_KEY,'Content-Type':'application/json'},json={'q':q,'gl':'tr','hl':'tr','location':'Turkey','num':40},timeout=15)
        if not r.ok:
            CACHE[k]={'ts':time.time(),'ref':None};_save();return None,f'http-{r.status_code}'
        vals=[]
        for it in r.json().get('shopping') or []:
            it_title=it.get('title') or ''
            if _score(title,it_title)<0.55:continue
            p=_price(it.get('price'))
            if p and current*1.08<p<current*3.0:vals.append(p)
        if vals:
            vals=sorted(vals)
            # Median is safer than the maximum/list price and resists outliers.
            ref=float(statistics.median(vals))
            CACHE[k]={'ts':time.time(),'ref':ref};_save();return ref,'serper-market'
        CACHE[k]={'ts':time.time(),'ref':None};_save();return None,'no-match'
    except Exception as e:
        return None,f'error-{type(e).__name__}'
