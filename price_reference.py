import os,re,time,json,hashlib,statistics,requests
from pathlib import Path

SERPER_API_KEY=(os.environ.get('SERPER_API_KEY') or '').strip()
CACHE_FILE=Path('/app/data/market_reference_cache.json')
CACHE_FILE.parent.mkdir(parents=True,exist_ok=True)
try:CACHE=json.loads(CACHE_FILE.read_text('utf-8')) if CACHE_FILE.exists() else {}
except Exception:CACHE={}

STOP={'urun','ürün','adet','paket','set','yeni','model','siyah','beyaz','icin','için','ile','ve','the','firsat','fırsat','indirim','kampanya','sepette','gecerli','geçerli'}

def _tokens(text):return {x for x in re.findall(r'[a-zçğıöşü0-9]{2,}',(text or '').lower()) if x not in STOP}
def _score(a,b):
    aa,bb=_tokens(a),_tokens(b)
    if not aa or not bb:return 0
    return len(aa&bb)/max(1,min(len(aa),len(bb)))
def _price(v):
    if v is None:return None
    s=re.sub(r'[^0-9,.]','',str(v))
    if not s:return None
    if ',' in s and '.' in s:s=s.replace('.','').replace(',','.') if s.rfind(',')>s.rfind('.') else s.replace(',','')
    elif ',' in s:
        a,b=s.rsplit(',',1);s=a.replace('.','')+'.'+b if len(b)<=2 else s.replace(',','')
    elif '.' in s:
        a,b=s.rsplit('.',1);s=s.replace('.','') if len(b)>2 else s
    try:
        x=float(s);return x if 1<x<10000000 else None
    except:return None
def _key(title):return hashlib.sha1(re.sub(r'\s+',' ',(title or '').lower()).strip().encode()).hexdigest()
def _save():
    try:CACHE_FILE.write_text(json.dumps(CACHE,ensure_ascii=False),'utf-8')
    except Exception:pass

def market_snapshot(title):
    """Exact/close product market snapshot from Serper Shopping.
    Returns (floor, median, sample_count, source). Values include all matching sellers,
    not only prices above the current one. Cached 2 hours.
    """
    if not SERPER_API_KEY or not title:return None,None,0,'none'
    k='snap:'+_key(title);x=CACHE.get(k)
    if isinstance(x,dict) and time.time()-float(x.get('ts') or 0)<7200:
        return _price(x.get('floor')),_price(x.get('median')),int(x.get('n') or 0),'cache'
    try:
        q=re.sub(r'\s+',' ',title).strip()[:180]
        r=requests.post('https://google.serper.dev/shopping',headers={'X-API-KEY':SERPER_API_KEY,'Content-Type':'application/json'},json={'q':q,'gl':'tr','hl':'tr','location':'Turkey','num':60},timeout=15)
        if not r.ok:return None,None,0,f'http-{r.status_code}'
        vals=[]
        for it in r.json().get('shopping') or []:
            if _score(title,it.get('title') or '')<0.60:continue
            p=_price(it.get('price'))
            if p:vals.append(p)
        # Deduplicate nearly-identical seller prices and reject gross outliers.
        vals=sorted(vals)
        clean=[]
        for p in vals:
            if not clean or abs(p-clean[-1])/max(p,1)>0.002:clean.append(p)
        if len(clean)>=2:
            med=float(statistics.median(clean))
            clean=[p for p in clean if med*0.45<=p<=med*2.2]
        if not clean:
            CACHE[k]={'ts':time.time(),'floor':None,'median':None,'n':0};_save();return None,None,0,'no-match'
        floor=min(clean);med=float(statistics.median(clean));n=len(clean)
        CACHE[k]={'ts':time.time(),'floor':floor,'median':med,'n':n};_save()
        return floor,med,n,'serper-market'
    except Exception as e:return None,None,0,f'error-{type(e).__name__}'

def market_reference(site,title,current):
    """Backward-compatible conservative reference.
    Only returns market median when the current price is actually below it.
    """
    floor,med,n,src=market_snapshot(title)
    if med and current and med>current*1.08:return med,src
    return None,src
