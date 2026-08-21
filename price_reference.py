import os,re,time,json,hashlib,statistics,requests,math
from pathlib import Path

SERPER_API_KEY=(os.environ.get('SERPER_API_KEY') or '').strip()
SERPER_DISABLED=str(os.environ.get('SERPER_DISABLED','0')).strip().lower() in {'1','true','yes','on'}
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
    return {x for x in re.findall(r'\b\d{2,4}\b',(text or '').lower()) if x not in {'2024','2025','2026'}}
def _score(a,b):
    aa,bb=_tokens(a),_tokens(b)
    if not aa or not bb:return 0
    return len(aa&bb)/max(1,min(len(aa),len(bb)))
def _identity_ok(query_title,result_title):
    qa=_model_tokens(query_title);qb=_model_tokens(result_title)
    if qa and not (qa & qb):return False
    na=_numbers(query_title);nb=_numbers(result_title)
    if na and len(na)<=4 and not (na&nb):
        if not (qa&qb):return False
    return _score(query_title,result_title)>=0.72

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

def _robust_prices(vals):
    vals=sorted(set(round(float(v),2) for v in vals if v and v>1))
    if len(vals)<2:return []
    floor=vals[0]
    close=[p for p in vals if p<=floor*1.65]
    if len(close)<2:return []
    vals=close
    if len(vals)>=4:
        logs=sorted(math.log(p) for p in vals)
        q1=statistics.median(logs[:len(logs)//2])
        q3=statistics.median(logs[(len(logs)+1)//2:])
        iqr=max(q3-q1,0.04)
        lo,hi=q1-1.25*iqr,q3+1.25*iqr
        trimmed=[p for p in vals if lo<=math.log(p)<=hi]
        if len(trimmed)>=2:vals=trimmed
    floor=min(vals);med=float(statistics.median(vals))
    if med>floor*1.45:return []
    return vals

def market_snapshot(title):
    if not title:return None,None,0,'none'
    k='snap5:'+_key(title);x=CACHE.get(k)
    if isinstance(x,dict) and time.time()-float(x.get('ts') or 0)<7200:
        return _price(x.get('floor')),_price(x.get('median')),int(x.get('n') or 0),'cache'
    if SERPER_DISABLED:return None,None,0,'api-free'
    if not SERPER_API_KEY:return None,None,0,'none'
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
        clean=_robust_prices(vals)
        if len(clean)<2:
            CACHE[k]={'ts':time.time(),'floor':None,'median':None,'n':len(clean)};_save();return None,None,len(clean),'insufficient-stable-matches'
        floor=min(clean);med=float(statistics.median(clean));n=len(clean)
        CACHE[k]={'ts':time.time(),'floor':floor,'median':med,'n':n};_save()
        return floor,med,n,'serper-market-robust-v5'
    except Exception as e:return None,None,0,f'error-{type(e).__name__}'

def market_reference(site,title,current):
    floor,med,n,src=market_snapshot(title)
    if n>=3 and med and current and med>current*1.08:return med,src
    return None,src
