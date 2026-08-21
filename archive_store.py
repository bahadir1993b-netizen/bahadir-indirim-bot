import os,re,sqlite3,statistics
from datetime import datetime,timezone,timedelta

DB_PATH=os.environ.get('LOCAL_PRICE_DB','/app/data/price_memory.db')

def _conn():
    os.makedirs(os.path.dirname(DB_PATH),exist_ok=True)
    c=sqlite3.connect(DB_PATH,timeout=30);c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA synchronous=NORMAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS title_prices(
      id INTEGER PRIMARY KEY AUTOINCREMENT,title_key TEXT NOT NULL,title TEXT,site TEXT,price REAL NOT NULL,
      old_price REAL,source TEXT,source_kind TEXT,product_url TEXT,recorded_at TEXT NOT NULL,
      UNIQUE(title_key,price,source,recorded_at));
    CREATE INDEX IF NOT EXISTS idx_title_prices_key_time ON title_prices(title_key,recorded_at DESC);
    CREATE TABLE IF NOT EXISTS archive_cursor(source TEXT PRIMARY KEY,cursor TEXT,updated_at TEXT);
    ''')
    return c

def tokens(title):
    stop={'urun','ürün','firsat','fırsat','indirim','kampanya','adet','paket','set','yeni','model','tl','icin','için','ve','ile','the','amazon','hepsiburada','trendyol'}
    return [x for x in re.findall(r'[a-zçğıöşü0-9]{2,}',(title or '').lower()) if x not in stop]

def key(title):
    ts=tokens(title);model=[x for x in ts if any(ch.isdigit() for ch in x) and any(ch.isalpha() for ch in x)]
    return ' '.join((model[:3]+[x for x in ts if x not in model][:7])[:10])

def add(title,price,site='',old_price=None,source='',source_kind='archive',url='',recorded_at=None):
    try:price=float(price)
    except:return False
    if not title or price<=0:return False
    k=key(title)
    if len(k)<3:return False
    dt=recorded_at or datetime.now(timezone.utc).isoformat();c=_conn()
    c.execute('INSERT OR IGNORE INTO title_prices(title_key,title,site,price,old_price,source,source_kind,product_url,recorded_at) VALUES(?,?,?,?,?,?,?,?,?)',
      (k,title,site,price,float(old_price) if old_price else None,source,source_kind,url,dt));c.commit();c.close();return True

def _score_keys(query_key,row_key):
    a=set(query_key.split());b=set(row_key.split())
    if not a or not b:return 0
    model_a={x for x in a if any(c.isdigit() for c in x)};model_b={x for x in b if any(c.isdigit() for c in x)}
    return len(a&b)/max(1,len(a))+(0.35 if model_a and model_a&model_b else 0)

def history_by_title(title,days=365,limit=300):
    qk=key(title);cut=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat();c=_conn()
    rows=c.execute('SELECT * FROM title_prices WHERE recorded_at>=? ORDER BY recorded_at DESC LIMIT 4000',(cut,)).fetchall();c.close();scored=[]
    for r in rows:
        s=_score_keys(qk,r['title_key'])
        if s>=0.55:scored.append((s,dict(r)))
    scored.sort(key=lambda x:(x[0],x[1]['recorded_at']),reverse=True);return [r for _,r in scored[:limit]]

def _median(vals):return statistics.median(vals) if vals else None

def smart_reference(title,current,url_history=None,page_old=None,stored_old=None):
    """Reference engine resistant to deal-post bias and nominal price drift.
    Deal-channel prices and 6-month lows can reject fake deals, but never create a high baseline by themselves.
    Normal archive/direct/comparison observations form the baseline. Older observations receive a bounded product-trend adjustment.
    """
    now=datetime.now(timezone.utc);url_history=url_history or [];title_rows=history_by_title(title,365,500)
    strong=[];deals=[];explicit=[]
    if page_old and current*1.03<float(page_old)<=current*2.2:explicit.append(float(page_old))
    if stored_old and current*1.03<float(stored_old)<=current*2.2:explicit.append(float(stored_old))
    for r in title_rows:
        try:p=float(r['price'])
        except:continue
        try:dt=datetime.fromisoformat(str(r['recorded_at']).replace('Z','+00:00'))
        except:dt=now
        age=max(0,(now-dt).days);kind=(r.get('source_kind') or '').lower()
        op=r.get('old_price')
        if op:
            try:op=float(op)
            except:op=0
            if current*1.03<op<=current*2.2:explicit.append(op)
        if ('telegram' in kind) or ('deal' in kind) or ('history-low' in kind):deals.append((p,age))
        else:strong.append((p,age))
    for r in url_history:
        try:p=float(r.get('price') if isinstance(r,dict) else r[0])
        except:continue
        src=str(r.get('source','') if isinstance(r,dict) else (r[2] if len(r)>2 else '')).lower()
        dtstr=r.get('recorded_at') if isinstance(r,dict) else (r[3] if len(r)>3 else None)
        try:dt=datetime.fromisoformat(str(dtstr).replace('Z','+00:00'))
        except:dt=now
        age=max(0,(now-dt).days)
        if 'telegram' in src or 'deal' in src:deals.append((p,age))
        else:strong.append((p,age))
    recent=[p for p,a in strong if a<=60 and current*0.55<=p<=current*2.2]
    older=[p for p,a in strong if 90<=a<=300 and current*0.45<=p<=current*2.2]
    trend=1.0
    if len(recent)>=2 and len(older)>=2:trend=max(0.85,min(1.35,_median(recent)/max(_median(older),1)))
    adjusted=[]
    for p,a in strong:
        if not (current*0.55<=p<=current*2.2):continue
        age_factor=min(1.0,a/180.0);adjusted.append(p*(1+(trend-1)*age_factor*0.65))
    if explicit:
        e=min(explicit)
        if adjusted:
            cap=statistics.quantiles(adjusted,n=4)[2] if len(adjusted)>=4 else max(adjusted);e=min(e,cap*1.12)
        if e>current*1.03:return e,'explicit-old'
    if len(adjusted)>=2:
        vals=sorted(adjusted);ref=_median(vals)
        if len(vals)>=4:ref=min(ref,statistics.quantiles(vals,n=4)[2])
        # If known deal floors are already at or below today's price, current is not special enough.
        recent_deals=[p for p,a in deals if a<=120]
        if recent_deals and current>=min(recent_deals)*0.98:return None,'deal-history-not-low'
        if ref>current*1.03:return float(ref),'weighted-history-trend'
    if len(deals)>=2:
        recent_deals=[p for p,a in deals if a<=120]
        if recent_deals and current>=min(recent_deals)*0.98:return None,'deal-history-not-low'
    return None,'none'

def cursor_get(source):
    c=_conn();r=c.execute('SELECT cursor FROM archive_cursor WHERE source=?',(source,)).fetchone();c.close();return r['cursor'] if r else None

def cursor_set(source,cursor):
    c=_conn();c.execute('INSERT INTO archive_cursor(source,cursor,updated_at) VALUES(?,?,?) ON CONFLICT(source) DO UPDATE SET cursor=excluded.cursor,updated_at=excluded.updated_at',(source,str(cursor),datetime.now(timezone.utc).isoformat()));c.commit();c.close()

def stats():
    c=_conn();n=c.execute('SELECT count(*) n FROM title_prices').fetchone()['n'];k=c.execute('SELECT count(distinct title_key) n FROM title_prices').fetchone()['n'];c.close();return {'title_prices':n,'title_keys':k}
