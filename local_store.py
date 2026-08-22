import os, sqlite3, threading
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse

DB_PATH=os.environ.get('LOCAL_PRICE_DB','/app/data/price_memory.db')
_lock=threading.Lock()

def _now(): return datetime.now(timezone.utc).isoformat()

def canonical(url):
    try:
        p=urlparse(url or '')
        if not p.netloc:return url or ''
        return urlunparse(('https',p.netloc.lower(),p.path.rstrip('/'),'','',''))
    except:return url or ''

def conn():
    os.makedirs(os.path.dirname(DB_PATH),exist_ok=True)
    c=sqlite3.connect(DB_PATH,timeout=20)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA synchronous=NORMAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS products(
      url TEXT PRIMARY KEY,
      site TEXT,
      title TEXT,
      image TEXT,
      first_seen TEXT,
      last_seen TEXT,
      last_price REAL,
      last_old_price REAL,
      source TEXT,
      source_post_id TEXT,
      check_count INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS prices(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      url TEXT NOT NULL,
      site TEXT,
      price REAL NOT NULL,
      old_price REAL,
      source TEXT,
      source_post_id TEXT,
      recorded_at TEXT NOT NULL,
      UNIQUE(url,price,source_post_id,recorded_at)
    );
    CREATE INDEX IF NOT EXISTS idx_prices_url_time ON prices(url,recorded_at DESC);
    CREATE INDEX IF NOT EXISTS idx_products_last_seen ON products(last_seen);
    CREATE TABLE IF NOT EXISTS telegram_posts(
      source TEXT NOT NULL,
      post_id TEXT NOT NULL,
      seen_at TEXT NOT NULL,
      PRIMARY KEY(source,post_id)
    );
    CREATE TABLE IF NOT EXISTS harvest_cursor(
      source TEXT PRIMARY KEY,
      before_id INTEGER,
      updated_at TEXT
    );
    ''')
    return c

def upsert_product(url,site='',title='',price=None,old_price=None,source='',post_id='',image=''):
    url=canonical(url)
    if not url:return
    now=_now()
    with _lock:
        c=conn()
        c.execute('''INSERT INTO products(url,site,title,image,first_seen,last_seen,last_price,last_old_price,source,source_post_id,check_count)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(url) DO UPDATE SET
                       site=COALESCE(NULLIF(excluded.site,''),products.site),
                       title=CASE WHEN length(excluded.title)>length(products.title) THEN excluded.title ELSE products.title END,
                       image=COALESCE(NULLIF(excluded.image,''),products.image),
                       last_seen=excluded.last_seen,
                       last_price=COALESCE(excluded.last_price,products.last_price),
                       last_old_price=COALESCE(excluded.last_old_price,products.last_old_price),
                       source=COALESCE(NULLIF(excluded.source,''),products.source),
                       source_post_id=COALESCE(NULLIF(excluded.source_post_id,''),products.source_post_id),
                       check_count=products.check_count+1''',
                  (url,site,title,image,now,now,price,old_price,source,str(post_id or ''),1))
        c.commit();c.close()

def add_price(url,site,price,old_price=None,source='',post_id='',recorded_at=None):
    if not price:return
    url=canonical(url); recorded_at=recorded_at or _now()
    upsert_product(url,site,price=price,old_price=old_price,source=source,post_id=post_id)
    with _lock:
        c=conn()
        row=c.execute('SELECT price,recorded_at FROM prices WHERE url=? ORDER BY recorded_at DESC LIMIT 1',(url,)).fetchone()
        should=True
        if row and abs(float(row['price'])-float(price))/max(float(price),1)<0.002:
            try:
                from datetime import datetime, timezone
                dt=datetime.fromisoformat(str(row['recorded_at']).replace('Z','+00:00'))
                should=(datetime.now(timezone.utc)-dt).total_seconds()>=1800
            except:pass
        if should:
            c.execute('INSERT OR IGNORE INTO prices(url,site,price,old_price,source,source_post_id,recorded_at) VALUES(?,?,?,?,?,?,?)',
                      (url,site,float(price),float(old_price) if old_price else None,source,str(post_id or ''),recorded_at))
        c.commit();c.close()

def history(url,days=180,limit=500):
    url=canonical(url)
    with _lock:
        c=conn()
        rows=c.execute("SELECT price,old_price,source,recorded_at FROM prices WHERE url=? AND recorded_at>=datetime('now',?) ORDER BY recorded_at DESC LIMIT ?",
                       (url,f'-{int(days)} days',int(limit))).fetchall()
        c.close()
    return [dict(r) for r in rows]

def list_products(limit=500):
    with _lock:
        c=conn();rows=c.execute('SELECT * FROM products ORDER BY last_seen ASC LIMIT ?',(int(limit),)).fetchall();c.close()
    return [dict(r) for r in rows]

def mark_post(source,post_id):
    with _lock:
        c=conn();c.execute('INSERT OR IGNORE INTO telegram_posts(source,post_id,seen_at) VALUES(?,?,?)',(source,str(post_id),_now()));c.commit();c.close()

def post_seen(source,post_id):
    with _lock:
        c=conn();r=c.execute('SELECT 1 FROM telegram_posts WHERE source=? AND post_id=?',(source,str(post_id))).fetchone();c.close()
    return bool(r)

def get_cursor(source):
    with _lock:
        c=conn();r=c.execute('SELECT before_id FROM harvest_cursor WHERE source=?',(source,)).fetchone();c.close()
    return int(r['before_id']) if r and r['before_id'] is not None else None

def set_cursor(source,before_id):
    with _lock:
        c=conn();c.execute('''INSERT INTO harvest_cursor(source,before_id,updated_at) VALUES(?,?,?)
          ON CONFLICT(source) DO UPDATE SET before_id=excluded.before_id,updated_at=excluded.updated_at''',(source,int(before_id),_now()));c.commit();c.close()
