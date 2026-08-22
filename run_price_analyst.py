import os,sqlite3,json,html,re,requests
from datetime import datetime,timezone,timedelta
from urllib.parse import urlparse
import archive_store as ar
import local_store as ls
import publish_core as pc

DB=os.environ.get('LOCAL_PRICE_DB','/app/data/price_memory.db')
TOKEN=os.environ.get('TELEGRAM_BOT_TOKEN','').strip();CHANNEL=os.environ.get('TELEGRAM_CHANNEL_ID','-1004424116637')
MIN=float(os.environ.get('MIN_DISCOUNT','15'));MAX_ALERTS=max(1,int(os.environ.get('ANALYST_MAX_ALERTS','4')))
PUBLISH=str(os.environ.get('ANALYST_PUBLISH','0')).strip().lower() in {'1','true','yes','on'}

def conn():
    c=sqlite3.connect(DB,timeout=30);c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL');c.executescript('CREATE TABLE IF NOT EXISTS analyst_alerts(title_key TEXT PRIMARY KEY,price REAL,alerted_at TEXT);');return c

def site_of(u):
    h=urlparse(u or '').netloc.lower()
    if 'amazon.com.tr' in h:return 'Amazon'
    if 'hepsiburada.com' in h:return 'Hepsiburada'
    if 'trendyol.com' in h:return 'Trendyol'
    if 'n11.com' in h:return 'N11'
    return ''

def fmt(x):return f'{x:,.2f}'.replace(',','X').replace('.',',').replace('X','.')
def already(url,p):return ls.recently_published(url,p,days=30,min_drop=.05)
def mark(url,p):ls.mark_published(url,p,'price-analyst')

def send(title,current,ref,url,source):
    if not PUBLISH:return False
    site=site_of(url);meta=pc.resolve_meta(url,site,title);title=pc.clean_title(meta.get('title') or title);image=meta.get('image')
    if not title:
        print(f'ANALİST YAYIN ENGELLENDİ | ürün_adı_yok | {site} | {url}');return False
    out=pc.affiliate_url(meta.get('resolved_url') or url)
    if site=='Amazon' and not pc.affiliate_ok(out):raise RuntimeError('Amazon affiliate tag missing at publish boundary')
    if already(url,current):print(f'ANALİST TEKRAR ENGELLENDİ | {site} | {current:.2f} | {title[:70]}');return False
    disc=(ref-current)/ref*100;text='\n'.join([f'🔥 %{disc:.0f} İNDİRİM','',f'🛍️ {html.escape(title)}',f'💰 {fmt(current)} TL',f'🏷️ Referans fiyat: {fmt(ref)} TL',f'🛍️ {site or source}','','👇 Fırsata git'])
    kb={'inline_keyboard':[[{'text':'🛒 FIRSATA GİT','url':out}]]};r=None
    if image:
        try:
            im=requests.get(image,headers=pc.HEAD,timeout=10,allow_redirects=True)
            if im.ok and len(im.content)>4000:r=requests.post(f'https://api.telegram.org/bot{TOKEN}/sendPhoto',data={'chat_id':CHANNEL,'caption':text[:1024],'parse_mode':'HTML','reply_markup':json.dumps(kb,ensure_ascii=False)},files={'photo':('product.jpg',im.content,im.headers.get('content-type','image/jpeg'))},timeout=20)
        except Exception as e:print(f'ANALİST FOTO UYARI | {type(e).__name__}')
    if not r or not r.ok:r=requests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage',json={'chat_id':CHANNEL,'text':text,'parse_mode':'HTML','disable_web_page_preview':True,'reply_markup':kb},timeout=15)
    if not r.ok:raise RuntimeError(f'Telegram {r.status_code}: {r.text[:160]}')
    mark(url,current);print(f'ANALİST YAYIN | {site} | foto={"var" if image else "yok"} | affiliate={"ok" if site!="Amazon" or pc.affiliate_ok(out) else "HATA"}');return True

def main():
    ls.runtime_start('price-analyst');profiles=ar.refresh_profiles(2500);now=datetime.now(timezone.utc);signals=[];errors=sent=0
    try:
        c=conn();rows=c.execute("SELECT * FROM title_prices WHERE recorded_at>=? AND product_url<>'' ORDER BY recorded_at DESC",((now-timedelta(hours=6)).isoformat(),)).fetchall();c.close();by={}
        for r in rows:
            k=r['title_key']
            if k not in by:by[k]=dict(r)
        for p in profiles:
            k=p['title_key'];r=by.get(k)
            if not r:continue
            url=r.get('product_url') or '';current=float(r['price'])
            if site_of(url) not in {'Amazon','Hepsiburada','Trendyol','N11'} or already(url,current):continue
            bases=[x for x in [p.get('normal_30'),p.get('normal_90')] if x and x>current*1.03]
            if not bases:continue
            ref=min(bases);disc=(ref-current)/ref*100
            if disc>=MIN:signals.append((disc,p,r,current,ref))
        signals.sort(key=lambda x:x[0],reverse=True)
        for disc,p,r,current,ref in signals[:MAX_ALERTS]:
            try:
                if send(r.get('title') or p.get('title') or '',current,ref,r.get('product_url') or '',r.get('source') or ''):sent+=1
            except Exception as e:errors+=1;print(f'ANALİST HATA | {type(e).__name__}: {e}')
        ls.runtime_finish('price-analyst','ok' if errors==0 else 'warning',candidates=len(signals),checked=min(len(signals),MAX_ALERTS),sent=sent,errors=errors,details={'publish':PUBLISH})
        print(f'=== FİYAT ANALİSTİ | sinyal={len(signals)} | gönderilen={sent} | hata={errors} | yayın={"AÇIK" if PUBLISH else "KAPALI"} ===')
    except Exception as e:
        ls.runtime_finish('price-analyst','error',candidates=len(signals),checked=0,sent=sent,errors=errors+1,details={'error':type(e).__name__});raise
if __name__=='__main__':main()