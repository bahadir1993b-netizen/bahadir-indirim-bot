import os,re,time,requests
from datetime import datetime,timezone
from bs4 import BeautifulSoup
import telegram_sources as ts
import local_store as ls
import archive_store as ar

PAGES=max(1,int(os.environ.get('HARVEST_PAGES_PER_RUN','16')))
MAX_BLOCKS=max(20,int(os.environ.get('HARVEST_MAX_BLOCKS','900')))
HEAD=dict(ts.HEAD);HEAD.update({'Cache-Control':'no-cache','Pragma':'no-cache'})

def post_id_of(b):
    try:return int((b.get('data-post') or '').split('/')[-1])
    except:return None

def extract_links(b):
    out=[]
    for a in b.select('a[href]'):
        u=ts.clean(a.get('href') or '')
        if u.startswith(('http://','https://')) and u not in out:out.append(u)
    return out

def best_product_url(links,title):
    for u in links:
        s=ts.site(u)
        if s and ts.valid(s,u):return s,ts.normalize(s,u)
    for u in links[:8]:
        s=ts.site(u)
        if s in {'Amazon','Hepsiburada','Trendyol'}:
            r=ts.http_resolve(u,s)
            if r:return s,r
        elif any(x in u for x in ts.SHORT):
            for candidate in ('Amazon','Hepsiburada','Trendyol'):
                r=ts.http_resolve(u,candidate)
                if r:return candidate,r
    return None,None

def harvest_source(source,channel):
    before=ls.get_cursor(source);saved=0;title_saved=0;scanned=0;oldest=before
    for page_no in range(PAGES):
        url=f'https://t.me/s/{channel}'+(f'?before={before}' if before else '')
        try:r=requests.get(url,headers=HEAD,timeout=12)
        except Exception as e:
            print(f'HARVEST {source} hata: {type(e).__name__}: {e}');break
        if not r.ok:
            print(f'HARVEST {source} HTTP {r.status_code}');break
        blocks=BeautifulSoup(r.text,'html.parser').select('.tgme_widget_message')
        if not blocks:break
        ids=[x for x in (post_id_of(b) for b in blocks) if x]
        if ids:oldest=min(ids);before=oldest
        for b in blocks:
            if scanned>=MAX_BLOCKS:break
            pid=post_id_of(b);tx=b.select_one('.tgme_widget_message_text')
            if not pid or not tx:continue
            scanned+=1
            raw=tx.get_text(' ',strip=True);title=ts.extract_title(raw);current,previous=ts.source_pair(raw)
            if not current:continue
            dt=None;tm=b.select_one('time[datetime]')
            if tm:
                try:dt=datetime.fromisoformat(tm['datetime'].replace('Z','+00:00')).astimezone(timezone.utc).isoformat()
                except:dt=None
            site,urlp=best_product_url(extract_links(b),title)
            # Always keep title+price: deal posts are valuable lower-bound observations even when the outbound link is gone.
            if ar.add(title,current,site or '',previous,source,'telegram-deal',urlp or '',dt):title_saved+=1
            if urlp:
                ls.upsert_product(urlp,site,title,current,previous,source,str(pid));ls.add_price(urlp,site,current,previous,'telegram-deal',str(pid),dt);saved+=1
            ls.mark_post(source,pid)
        print(f'HARVEST {source} sayfa={page_no+1}/{PAGES} blok={len(blocks)} url_kayit={saved} baslik_fiyat={title_saved} before={before}')
        if scanned>=MAX_BLOCKS:break
        time.sleep(.25)
    if oldest:ls.set_cursor(source,oldest)
    return scanned,saved,title_saved

def main():
    print(f'=== ÜCRETSİZ GEÇMİŞ FİYAT HASADI V2 | kaynak={len(ts.SOURCES)} | sayfa/kaynak={PAGES} ===')
    total_scan=total_save=total_title=0
    for source,channel in ts.SOURCES.items():
        a,b,c=harvest_source(source,channel);total_scan+=a;total_save+=b;total_title+=c
    st=ls.stats();ast=ar.stats()
    print(f'=== HASAT BİTTİ | taranan={total_scan} | url_fiyat={total_save} | baslik_fiyat={total_title} | lokal_urun={st["products"]} | lokal_fiyat={st["prices"]} | arsiv={ast} ===')

if __name__=='__main__':main()
