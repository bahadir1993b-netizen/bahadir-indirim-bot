import os,re,time,json,hashlib,statistics,requests
from pathlib import Path

SERPER_API_KEY=(os.environ.get('SERPER_API_KEY') or '').strip()
CACHE_FILE=Path('/app/data/market_reference_cache.json')
CACHE_FILE.parent.mkdir(parents=True,exist_ok=True)
try:CACHE=json.loads(CACHE_FILE.read_text('utf-8')) if CACHE_FILE.exists() else {}
except Exception:CACHE={}

STOP={'urun','ürün','adet','paket','set','yeni','model','siyah','beyaz','icin','için','ile','ve','the','firsat','fırsat','indirim','kampanya','sepette','gecerli','geçerli','kablosuz','bluetooth'}

def _tokens(text):return {x for x in re.findall(r'[a-zçğıöşü0-9]{2,}',(text or '').lower()) if x not in STOP}
def _model_tokens(text):
    out=set()
    for x in re.findall(r'[a-z0-9-]{3,}',(text or '').lower()):
        if any(c.isalpha() for c in x) and any(c.isdigit() for c in x):out.add(x.strip('-'))
    return out
def _numbers(text):
    # Product-defining sizes/counts, but ignore tiny generic numbers.
    return {x for x in re.findall(r'\b\d{2,4}\b',(text or '').lower()) if x not in {'2024','2025','2026'}}
def _score(a,b):
    aa,bb=_tokens(a),_tokens(b)
    if not aa or not bb:return 0
    return len(aa&bb)/max(1,min(len(aa),len(bb)))
def _identity_ok(query_title,result_title):
    qa=_model_tokens(query_title);qb=_model_tokens(result_title)
    if qa and not (qa & qb):return False
    # If query has a distinctive screen/capacity/count number, require at least one shared number.
    na=_numbers(query_title);nb=_numbers(result_title)
    if na and len(na)<=4 and not (na&nb):
        # Model token agreement is stronger than generic number agreement.
        if not (qa&qb):return False
    return _score(query_title,result_title)>=0.68

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
    if not SERPER_API_KEY or not title:return None,None,0,'none'
    k='snap2:'+_key(title);x=CACHE.get(k)
    if isinstance(x,dict) and time.time()-float(x.get('ts') or 0)<7200:
        return _price(x.get('floor')),_price(x.get('median')),int(x.get('n') or 0),'cache'
    try:
        q=re.sub(r'\s+',' ',title).strip()[:180]
        r=requests.post('https://google.serper.dev/shopping',headers={'X-API-KEY':SERPER_API_KEY,'Content-Type':'application/json'},json={'q':q,'gl':'tr','hl':'tr','location':'Turkey','num':60},timeout=15)
        if not r.ok:return None,None,0,f'http-{r.status_code}'
        vals=[]
        for it in r.json().get('shopping') or []:
            rt=it.get('title') or ''
            if not _identity_ok(title,rt):continue
            p=_price(it.get('price'))
            if p:vals.append(p)
        vals=sorted(vals);clean=[]
        for p in vals:
            if not clean or abs(p-clean[-1])/max(p,1)>0.002:clean.append(p)
        if len(clean)>=3:
            med=float(statistics.median(clean));clean=[p for p in clean if med*0.60<=p<=med*1.75]
        if not clean:
            CACHE[k]={'ts':time.time(),'floor':None,'median':None,'n':0};_save();return None,None,0,'no-exact-match'
        floor=min(clean);med=float(statistics.median(clean));n=len(clean)
        CACHE[k]={'ts':time.time(),'floor':floor,'median':med,'n':n};_save()
        return floor,med,n,'serper-market-strict'
    except Exception as e:return None,None,0,f'error-{type(e).__name__}'

def market_reference(site,title,current):
    floor,med,n,src=market_snapshot(title)
    if med and current and med>current*1.08:return med,src
    return None,src
