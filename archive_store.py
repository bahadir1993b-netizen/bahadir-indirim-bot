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
    CREATE INDEX IF NOT EXISTS idx_title_prices_time ON title_prices(recorded_at DESC);
    CREATE TABLE IF NOT EXISTS archive_cursor(source TEXT PRIMARY KEY,cursor TEXT,updated_at TEXT);
    CREATE TABLE IF NOT EXISTS price_profiles(
      title_key TEXT PRIMARY KEY,title TEXT,last_price REAL,last_source TEXT,last_kind TEXT,last_url TEXT,
      normal_30 REAL,normal_90 REAL,normal_365 REAL,deal_floor_120 REAL,trend_ratio REAL,samples INTEGER,
      updated_at TEXT NOT NULL);
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
    rows=c.execute('SELECT * FROM title_prices WHERE recorded_at>=? ORDER BY recorded_at DESC LIMIT 8000',(cut,)).fetchall();c.close();scored=[]
    for r in rows:
        s=_score_keys(qk,r['title_key'])
        if s>=0.55:scored.append((s,dict(r)))
    scored.sort(key=lambda x:(x[0],x[1]['recorded_at']),reverse=True);return [r for _,r in scored[:limit]]

def list_title_candidates(limit=1000,offset=0):
    """Unique title feed for enrichers even when no merchant URL was harvested yet."""
    c=_conn();rows=c.execute('''
      SELECT tp.* FROM title_prices tp
      JOIN (SELECT title_key,MAX(recorded_at) mx FROM title_prices GROUP BY title_key) x
        ON x.title_key=tp.title_key AND x.mx=tp.recorded_at
      WHERE length(tp.title)>=5
      GROUP BY tp.title_key
      ORDER BY tp.recorded_at DESC
      LIMIT ? OFFSET ?''',(int(limit),int(offset))).fetchall();c.close()
    return [dict(r) for r in rows]

def _median(vals):return statistics.median(vals) if vals else None

def _is_deal(kind):
    k=(kind or '').lower()
    return ('telegram' in k) or ('deal' in k) or ('history-low' in k) or ('campaign' in k)

def build_profile(title,days=365):
    """Separate normal-market observations from deal-floor observations and model nominal price drift."""
    rows=history_by_title(title,days,700);now=datetime.now(timezone.utc)
    normal=[];deals=[];latest=None
    for r in rows:
        try:p=float(r['price'])
        except:continue
        try:dt=datetime.fromisoformat(str(r['recorded_at']).replace('Z','+00:00'))
        except:dt=now
        age=max(0,(now-dt).total_seconds()/86400)
        item=(p,age,r)
        if latest is None or str(r.get('recorded_at',''))>str(latest[2].get('recorded_at','')):latest=item
        (deals if _is_deal(r.get('source_kind')) else normal).append(item)
    def med(max_age):
        vals=[p for p,a,_ in normal if a<=max_age]
        if not vals:return None
        vals=sorted(vals)
        # robust center: discard extreme high tail when enough samples exist
        if len(vals)>=6:vals=vals[:max(4,int(len(vals)*0.85))]
        return float(statistics.median(vals))
    n30,n90,n365=med(30),med(90),med(365)
    recent=[p for p,a,_ in normal if a<=45];older=[p for p,a,_ in normal if 120<=a<=365]
    trend=1.0
    if len(recent)>=2 and len(older)>=2:trend=max(.75,min(1.50,statistics.median(recent)/max(statistics.median(older),1)))
    floors=[p for p,a,_ in deals if a<=120];floor=min(floors) if floors else None
    last=latest[2] if latest else {}
    return {'title_key':key(title),'title':title,'last_price':latest[0] if latest else None,'last_source':last.get('source'),'last_kind':last.get('source_kind'),'last_url':last.get('product_url'),'normal_30':n30,'normal_90':n90,'normal_365':n365,'deal_floor_120':floor,'trend_ratio':trend,'samples':len(rows),'updated_at':now.isoformat()}

def save_profile(title):
    p=build_profile(title);c=_conn()
    c.execute('''INSERT INTO price_profiles(title_key,title,last_price,last_source,last_kind,last_url,normal_30,normal_90,normal_365,deal_floor_120,trend_ratio,samples,updated_at)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(title_key) DO UPDATE SET
      title=excluded.title,last_price=excluded.last_price,last_source=excluded.last_source,last_kind=excluded.last_kind,last_url=excluded.last_url,
      normal_30=excluded.normal_30,normal_90=excluded.normal_90,normal_365=excluded.normal_365,deal_floor_120=excluded.deal_floor_120,
      trend_ratio=excluded.trend_ratio,samples=excluded.samples,updated_at=excluded.updated_at''',tuple(p[k] for k in ['title_key','title','last_price','last_source','last_kind','last_url','normal_30','normal_90','normal_365','deal_floor_120','trend_ratio','samples','updated_at']))
    c.commit();c.close();return p

def refresh_profiles(limit=500):
    rows=list_title_candidates(limit);out=[]
    for r in rows:
        try:out.append(save_profile(r['title']))
        except:pass
    return out

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
        if _is_deal(kind):deals.append((p,age))
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
    c=_conn();n=c.execute('SELECT count(*) n FROM title_prices').fetchone()['n'];k=c.execute('SELECT count(distinct title_key) n FROM title_prices').fetchone()['n'];p=c.execute('SELECT count(*) n FROM price_profiles').fetchone()['n'];c.close();return {'title_prices':n,'title_keys':k,'profiles':p}
