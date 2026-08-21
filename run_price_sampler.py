import os,time,statistics
from datetime import datetime,timezone
import local_store as ls
import archive_store as ar
import run_direct_watch_v2 as v2

LIMIT=max(20,int(os.environ.get('PRICE_SAMPLE_LIMIT','220')))
SLEEP=max(0.05,float(os.environ.get('PRICE_SAMPLE_SLEEP','0.15')))
DEAL_PCT=max(5.0,float(os.environ.get('PRICE_SAMPLE_DEAL_PCT','15')))
CURSOR_KEY='price-sampler-index'

def num(x):
    try:return float(x)
    except:return None

def baseline(title,current):
    rows=ar.history_by_title(title,days=180,limit=250)
    vals=[]
    for r in rows:
        kind=(r.get('source_kind') or '').lower()
        if 'telegram' in kind or 'deal' in kind:continue
        try:p=float(r.get('price'))
        except:continue
        if current*0.55<=p<=current*2.0:vals.append(p)
    if len(vals)<3:return None
    vals=sorted(vals)
    if len(vals)>=8:
        q1=int(len(vals)*0.15);q2=max(q1+1,int(len(vals)*0.85));vals=vals[q1:q2]
    return float(statistics.median(vals)) if vals else None

def main():
    products=ls.list_products(100000)
    if not products:
        print('=== SÜREKLİ FİYAT ÖRNEKLEME | katalog=0 ===');return
    try:start=int(ar.cursor_get(CURSOR_KEY) or 0)
    except:start=0
    picked=[]
    n=len(products)
    for i in range(min(LIMIT,n)):
        picked.append(products[(start+i)%n])
    next_idx=(start+len(picked))%n
    ok=fail=normal=deal=changed=0
    print(f'=== SÜREKLİ FİYAT ÖRNEKLEME | katalog={n} | tur={len(picked)} | başlangıç={start} ===')
    for row in picked:
        url=row.get('url') or '';title=row.get('title') or 'Ürün';site=row.get('site') or ''
        expected=num(row.get('last_price'))
        try:info=v2.http_check(url,expected)
        except Exception:info=None
        if not info or not info.get('live'):
            fail+=1;time.sleep(SLEEP);continue
        p=float(info['live']);old=num(info.get('old'));base=baseline(title,p)
        kind='market-normal'
        if base and base>p and (base-p)/base*100>=DEAL_PCT:
            kind='market-deal';deal+=1
        else:normal+=1
        prev=num(row.get('last_price'))
        if prev and abs(prev-p)/max(p,1)>=0.005:changed+=1
        ls.upsert_product(url,site,info.get('title') or title,p,old,'price-sampler','',info.get('image') or '')
        ls.add_price(url,site,p,old,'price-sampler','')
        ar.add(info.get('title') or title,p,site,old,'PriceSampler',kind,url,datetime.now(timezone.utc).isoformat())
        ok+=1
        print(f'ÖRNEK: {site} | {p:.2f} TL | tip={kind} | baz={base or 0:.2f} | {title[:72]}')
        time.sleep(SLEEP)
    ar.cursor_set(CURSOR_KEY,next_idx)
    print(f'=== ÖRNEKLEME BİTTİ | başarılı={ok} | fiyat_yok={fail} | normal={normal} | fırsat={deal} | değişen={changed} | sonraki={next_idx} | arsiv={ar.stats()} ===')

if __name__=='__main__':main()
