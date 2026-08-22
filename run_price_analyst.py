import os,sqlite3,json,html,re,requests
from datetime import datetime,timezone,timedelta
from urllib.parse import urlparse,urlencode,urlunparse
from bs4 import BeautifulSoup
import archive_store as ar

DB=os.environ.get('LOCAL_PRICE_DB','/app/data/price_memory.db')
TOKEN=os.environ.get('TELEGRAM_BOT_TOKEN','').strip();CHANNEL=os.environ.get('TELEGRAM_CHANNEL_ID','-1004424116637')
MIN=float(os.environ.get('MIN_DISCOUNT','15'));MAX_ALERTS=max(1,int(os.environ.get('ANALYST_MAX_ALERTS','4')))
AMAZON_TAG=(os.environ.get('AMAZON_ASSOCIATE_TAG') or os.environ.get('AMAZON_TAG') or '').strip()
HEAD={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36','Accept-Language':'tr-TR,tr;q=0.9,en;q=0.8'}

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

def product_key(u,fallback=''):
    p=urlparse(u or '');h=p.netloc.lower().replace('www.','');path=p.path
    if 'amazon.com.tr' in h:
        m=re.search(r'/(?:dp|gp/product)/([A-Z0-9]{8,})',path,re.I)
        if m:return 'amazon:'+m.group(1).upper()
    if 'hepsiburada.com' in h:
        m=re.search(r'-p-([A-Za-z0-9]+)',path,re.I)
        if m:return 'hb:'+m.group(1).lower()
    if 'trendyol.com' in h:
        m=re.search(r'-p-(\d+)',path,re.I)
        if m:return 'ty:'+m.group(1)
    return 'url:'+urlunparse((p.scheme,h,path,'','','')).lower() if h else fallback

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
    age=datetime.now(timezone.utc)-dt;old=float(r['price'])
    return age<timedelta(days=30) and p>=old*.95

def stable_recent(k,p):
    c=conn();since=(datetime.now(timezone.utc)-timedelta(days=14)).isoformat()
    rows=c.execute('SELECT price FROM title_prices WHERE title_key=? AND recorded_at>=? ORDER BY recorded_at DESC LIMIT 200',(k,since)).fetchall();c.close()
    vals=[]
    for r in rows:
        try:x=float(r['price'])
        except:continue
        if x>0:vals.append(x)
    if len(vals)<4:return False
    near=[x for x in vals if abs(x-p)/max(p,1)<=.05]
    return len(near)>=4 and len(near)>=int(len(vals)*.60)

def mark(k,p):
    c=conn();c.execute('INSERT INTO analyst_alerts(title_key,price,alerted_at) VALUES(?,?,?) ON CONFLICT(title_key) DO UPDATE SET price=excluded.price,alerted_at=excluded.alerted_at',(k,p,datetime.now(timezone.utc).isoformat()));c.commit();c.close()

def page_meta(url):
    try:
        r=requests.get(url,headers=HEAD,timeout=10,allow_redirects=True)
        if not r.ok:return '',None
        soup=BeautifulSoup(r.text,'html.parser')
        title='';image=None
        for sel,attr in [('meta[property="og:title"]','content'),('h1',None)]:
            e=soup.select_one(sel)
            if e:
                title=re.sub(r'\s+',' ',(e.get(attr) if attr else e.get_text(' ',strip=True)) or '').strip()
                if title:break
        for sel in ['meta[property="og:image"]','meta[name="twitter:image"]']:
            e=soup.select_one(sel)
            if e and (e.get('content') or '').startswith('http'):image=e.get('content');break
        return title[:180],image
    except:return '',None

def send(title,current,ref,url,source):
    if not TOKEN or not CHANNEL:return False
    disc=(ref-current)/ref*100;site=site_of(url);u=outlink(url)
    page_title,image=page_meta(url)
    if page_title:title=page_title
    title=re.sub(r'\s+',' ',title or 'Ürün').strip()[:180]
    text='\n'.join([f'🔥 %{disc:.0f} İNDİRİM','',f'🛍️ {html.escape(title)}',f'💰 {fmt(current)} TL',f'🏷️ Referans fiyat: {fmt(ref)} TL',f'🛍️ {site or source}','','👇 <a href="'+html.escape(u,quote=True)+'"><b>Fırsata git</b></a>'])
    kb={'inline_keyboard':[[{'text':'🛒 FIRSATA GİT','url':u}]]}
    r=None
    if image:
        try:
            r=requests.post(f'https://api.telegram.org/bot{TOKEN}/sendPhoto',data={'chat_id':CHANNEL,'photo':image,'caption':text[:1024],'parse_mode':'HTML','reply_markup':json.dumps(kb,ensure_ascii=False)},timeout=18)
        except: r=None
    if not r or not r.ok:
        r=requests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage',json={'chat_id':CHANNEL,'text':text,'parse_mode':'HTML','disable_web_page_preview':True,'reply_markup':kb},timeout=15)
    return r.ok

def main():
    profiles=ar.refresh_profiles(2500);now=datetime.now(timezone.utc);signals=[]
    c=conn();rows=c.execute("SELECT * FROM title_prices WHERE recorded_at>=? AND product_url<>'' ORDER BY recorded_at DESC",((now-timedelta(hours=6)).isoformat(),)).fetchall();c.close()
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
        current=float(r['price']);dedupe=product_key(url,k)
        if already(dedupe,current):
            print(f'ANALİST ATLANDI | duplicate-product-30d | {current:.2f} | {str(r.get("title"))[:70]}');continue
        if stable_recent(k,current):
            print(f'ANALİST ATLANDI | stable-price-history | {current:.2f} | {str(r.get("title"))[:70]}');continue
        bases=[x for x in [p.get('normal_30'),p.get('normal_90')] if x and x>current*1.03]
        old=r.get('old_price')
        if old:
            try:
                old=float(old)
                if current*1.03<old<=current*1.45:bases.append(old)
            except:pass
        if not bases:continue
        ref=min(bases);disc=(ref-current)/ref*100
        floor=p.get('deal_floor_120')
        if floor and current>float(floor)*1.05:continue
        if disc>=MIN:signals.append((disc,p,r,current,ref,dedupe))
    signals.sort(key=lambda x:x[0],reverse=True);sent=0
    for disc,p,r,current,ref,dedupe in signals[:MAX_ALERTS]:
        if send(r.get('title') or p.get('title') or 'Ürün',current,ref,r.get('product_url') or '',r.get('source') or ''):
            mark(dedupe,current);sent+=1;print(f'ANALİST GÖNDERDİ | %{disc:.1f} | {current:.2f}->{ref:.2f} | {str(r.get("title"))[:70]}')
    print(f'=== FİYAT ANALİSTİ | profil={len(profiles)} | taze_linkli={len(by)} | sinyal={len(signals)} | gönderilen={sent} | arşiv={ar.stats()} ===')

if __name__=='__main__':main()
