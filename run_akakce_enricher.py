import os,re,time,requests
from urllib.parse import quote,urljoin
from bs4 import BeautifulSoup
from datetime import datetime,timezone
import local_store as ls
import archive_store as ar

LIMIT=max(5,int(os.environ.get('AKAKCE_LIMIT','40')))
SLEEP=max(.35,float(os.environ.get('AKAKCE_SLEEP','1.0')))
CURSOR_KEY='akakce-product-index-v2'
HEAD={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36','Accept-Language':'tr-TR,tr;q=0.9'}

def money(s):
    s=re.sub(r'[^0-9,.]','',s or '')
    if not s:return None
    if ',' in s and '.' in s:s=s.replace('.','').replace(',','.') if s.rfind(',')>s.rfind('.') else s.replace(',','')
    elif ',' in s:
        a,b=s.rsplit(',',1);s=a.replace('.','')+'.'+b if len(b)<=2 else s.replace(',','')
    elif '.' in s:
        a,b=s.rsplit('.',1);s=s.replace('.','') if len(b)>2 else s
    try:return float(s)
    except:return None

def toks(s):return {x for x in re.findall(r'[a-zçğıöşü0-9]{3,}',(s or '').lower()) if x not in {'urun','ürün','fiyat','fiyatları','model','yeni','icin','için','ile','ve','amazon','hepsiburada','trendyol'}}
def score(a,b):
    x,y=toks(a),toks(b)
    if not x or not y:return 0
    model={t for t in x if any(c.isdigit() for c in t)}
    return len(x&y)/len(x)+(0.40 if model and model&y else 0)

def search(title):
    url='https://www.akakce.com/arama/?q='+quote(' '.join(title.split())[:140])
    r=requests.get(url,headers=HEAD,timeout=12)
    if not r.ok:return None
    soup=BeautifulSoup(r.text,'html.parser');best=None
    for a in soup.select('a[href]'):
        txt=a.get_text(' ',strip=True);href=a.get('href') or ''
        if 'fiyati' not in href and 'en-ucuz' not in href:continue
        sc=score(title,txt+' '+href)
        if sc<0.54:continue
        parent=a.parent;context=(parent.get_text(' ',strip=True) if parent else txt)[:1200]
        m=re.search(r'En\s+Ucuz\s+([\d.,]+)\s*TL',context,re.I) or re.search(r'([\d.,]+)\s*TL',context,re.I)
        p=money(m.group(1)) if m else None
        if not p:continue
        full=urljoin('https://www.akakce.com',href);cand=(sc,p,txt.strip() or title,full)
        if best is None or cand[0]>best[0]:best=cand
    return best

def detail(title,url,current_price):
    try:r=requests.get(url,headers=HEAD,timeout=12)
    except:return None,None,None
    if not r.ok:return None,None,None
    text=BeautifulSoup(r.text,'html.parser').get_text(' ',strip=True);low=now=high=None
    for pat in [r'Dönem\s+İçi\s+En\s+Düşük\s+Fiyat\s*:?\s*([\d.,]+)\s*TL',r'En\s+Düşük\s+Fiyat\s*:?\s*([\d.,]+)\s*TL']:
        m=re.search(pat,text,re.I)
        if m:low=money(m.group(1));break
    for pat in [r'Dönem\s+İçi\s+En\s+Yüksek\s+Fiyat\s*:?\s*([\d.,]+)\s*TL',r'En\s+Yüksek\s+Fiyat\s*:?\s*([\d.,]+)\s*TL']:
        m=re.search(pat,text,re.I)
        if m:high=money(m.group(1));break
    for pat in [r'Şu\s+Anki\s+Fiyat\s*:?\s*([\d.,]+)\s*TL',r'En\s+Ucuz\s+([\d.,]+)\s*TL']:
        m=re.search(pat,text,re.I)
        if m:now=money(m.group(1));break
    return now or current_price,low,high

def candidate_pool():
    seen=set();out=[]
    # merchant-linked products first
    for r in ls.list_products(100000):
        t=(r.get('title') or '').strip();k=ar.key(t)
        if len(t)>=5 and k and k not in seen:seen.add(k);out.append({'title':t,'site':r.get('site') or '','url':r.get('url') or ''})
    # then every title collected from Telegram/OnuAl/other archives
    for r in ar.list_title_candidates(100000):
        t=(r.get('title') or '').strip();k=r.get('title_key') or ar.key(t)
        if len(t)>=5 and k and k not in seen:seen.add(k);out.append({'title':t,'site':r.get('site') or '','url':r.get('product_url') or ''})
    return out

def main():
    rows=candidate_pool()
    if not rows:
        print(f'=== AKAKÇE 24/7 ZENGİNLEŞTİRME V2 | aday=0 | limit/tur={LIMIT} ===');return
    try:start=int(ar.cursor_get(CURSOR_KEY) or 0)
    except:start=0
    n=len(rows);done=stored=0
    print(f'=== AKAKÇE 24/7 ZENGİNLEŞTİRME V2 | aday={n} | limit/tur={LIMIT} | başlangıç={start} ===')
    for i in range(min(LIMIT,n)):
        row=rows[(start+i)%n];title=row['title']
        done+=1
        try:
            hit=search(title)
            if not hit:
                print(f'AKAKCE BULAMADI | {title[:70]}');time.sleep(SLEEP);continue
            sc,p,matched,url=hit;now,low,high=detail(title,url,p);dt=datetime.now(timezone.utc).isoformat()
            if now and now>0:
                ar.add(title,now,row.get('site') or '',None,'Akakce','comparison-current',url,dt);stored+=1
            if low and low>0:
                ar.add(title,low,row.get('site') or '',None,'Akakce','comparison-history-low',url,dt);stored+=1
            if high and now and now*1.03<high<=now*3:
                ar.add(title,high,row.get('site') or '',None,'Akakce','comparison-history-high',url,dt);stored+=1
            prof=ar.save_profile(title)
            print(f'AKAKCE | skor={sc:.2f} | şimdi={now or 0:.2f} | 6ay_dip={low or 0:.2f} | yüksek={high or 0:.2f} | trend={prof.get("trend_ratio",1):.2f} | {title[:58]}')
        except Exception as e:print(f'AKAKCE HATA | {type(e).__name__}: {e}')
        time.sleep(SLEEP)
    next_idx=(start+min(LIMIT,n))%n;ar.cursor_set(CURSOR_KEY,next_idx)
    print(f'=== AKAKCE BİTTİ | kontrol={done} | kayıt={stored} | sonraki={next_idx}/{n} | arsiv={ar.stats()} ===')

if __name__=='__main__':main()
