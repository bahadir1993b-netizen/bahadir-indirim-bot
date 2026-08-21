import os,sqlite3,json,html,requests
from datetime import datetime,timezone,timedelta
from urllib.parse import urlparse,urlencode
import archive_store as ar

DB=os.environ.get('LOCAL_PRICE_DB','/app/data/price_memory.db')
TOKEN=os.environ.get('TELEGRAM_BOT_TOKEN','').strip();CHANNEL=os.environ.get('TELEGRAM_CHANNEL_ID','-1004424116637')
MIN=float(os.environ.get('MIN_DISCOUNT','15'));MAX_ALERTS=max(1,int(os.environ.get('ANALYST_MAX_ALERTS','4')))
AMAZON_TAG=(os.environ.get('AMAZON_ASSOCIATE_TAG') or os.environ.get('AMAZON_TAG') or '').strip()

def conn():
    c=sqlite3.connect(DB,timeout=30);c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''CREATE TABLE IF NOT EXISTS analyst_alerts(title_key TEXT PRIMARY KEY,price REAL,alerted_at TEXT);''')
    return c

def site_of(u):
    h=urlparse(u or '').netloc.lower()
    if 'amazon.com.tr' in h:return 'Amazon'
    if 'hepsiburada.com' in h:return 'Hepsiburada'
    if 'trendyol.com' in h:return 'Trendyol'
    return ''

def outlink(u):
    if site_of(u)=='Amazon' and AMAZON_TAG and 'tag=' not in u:
        sep='&' if '?' in u else '?';return u+sep+urlencode({'tag':AMAZON_TAG})
    return u

def fmt(x):return f'{x:,.2f}'.replace(',','X').replace('.',',').replace('X','.')

def already(k,p):
    c=conn();r=c.execute('SELECT price,alerted_at FROM analyst_alerts WHERE title_key=?',(k,)).fetchone();c.close()
    if not r:return False
    try:dt=datetime.fromisoformat(r['alerted_at'].replace('Z','+00:00'))
    except:return False
    return (datetime.now(timezone.utc)-dt)<timedelta(hours=12) and abs(float(r['price'])-p)/max(p,1)<.02

def mark(k,p):
    c=conn();c.execute('INSERT INTO analyst_alerts(title_key,price,alerted_at) VALUES(?,?,?) ON CONFLICT(title_key) DO UPDATE SET price=excluded.price,alerted_at=excluded.alerted_at',(k,p,datetime.now(timezone.utc).isoformat()));c.commit();c.close()

def send(title,current,ref,url,source):
    if not TOKEN or not CHANNEL:return False
    disc=(ref-current)/ref*100;site=site_of(url);u=outlink(url)
    text='\n'.join([f'🔥 %{disc:.0f} İNDİRİM','',f'🛍️ {html.escape(title[:260])}',f'💰 {fmt(current)} TL',f'🏷️ Referans fiyat: {fmt(ref)} TL',f'🛍️ {site or source}','','👇 <a href="'+html.escape(u,quote=True)+'"><b>Fırsata git</b></a>'])
    kb={'inline_keyboard':[[{'text':'🛒 FIRSATA GİT','url':u}]]}
    r=requests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage',json={'chat_id':CHANNEL,'text':text,'parse_mode':'HTML','reply_markup':kb},timeout=15)
    return r.ok

def main():
    profiles=ar.refresh_profiles(2500);now=datetime.now(timezone.utc);signals=[]
    c=conn()
    rows=c.execute("SELECT * FROM title_prices WHERE recorded_at>=? AND product_url<>'' ORDER BY recorded_at DESC",((now-timedelta(hours=6)).isoformat(),)).fetchall();c.close()
    by={}
    for r in rows:
        k=r['title_key']
        if k not in by:by[k]=dict(r)
    for p in profiles:
        k=p['title_key'];r=by.get(k)
        if not r:continue
        kind=(r.get('source_kind') or '').lower();url=r.get('product_url') or ''
        if 'telegram' not in kind and 'deal' not in kind:continue
        if site_of(url) not in {'Amazon','Hepsiburada','Trendyol'}:continue
        current=float(r['price']);bases=[x for x in [p.get('normal_30'),p.get('normal_90')] if x and x>current*1.03]
        old=r.get('old_price')
        if old:
            try:
                old=float(old)
                if current*1.03<old<=current*2.5:bases.append(old)
            except:pass
        if not bases:continue
        ref=min(bases);disc=(ref-current)/ref*100
        floor=p.get('deal_floor_120')
        # recent deal floors are a veto unless today's price is essentially at the floor
        if floor and current>float(floor)*1.05:continue
        if disc>=MIN and not already(k,current):signals.append((disc,p,r,current,ref))
    signals.sort(key=lambda x:x[0],reverse=True);sent=0
    for disc,p,r,current,ref in signals[:MAX_ALERTS]:
        if send(r.get('title') or p.get('title') or 'Ürün',current,ref,r.get('product_url') or '',r.get('source') or ''):
            mark(p['title_key'],current);sent+=1;print(f'ANALİST GÖNDERDİ | %{disc:.1f} | {current:.2f}->{ref:.2f} | {str(r.get("title"))[:70]}')
    print(f'=== FİYAT ANALİSTİ | profil={len(profiles)} | taze_linkli={len(by)} | sinyal={len(signals)} | gönderilen={sent} | arşiv={ar.stats()} ===')

if __name__=='__main__':main()
