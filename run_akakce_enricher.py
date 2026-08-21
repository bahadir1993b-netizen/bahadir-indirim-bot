import os,re,time,requests
from urllib.parse import quote,urljoin
from bs4 import BeautifulSoup
from datetime import datetime,timezone
import local_store as ls
import archive_store as ar

LIMIT=max(5,int(os.environ.get('AKAKCE_LIMIT','40')))
SLEEP=max(.25,float(os.environ.get('AKAKCE_SLEEP','0.7')))
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

def toks(s):return {x for x in re.findall(r'[a-zçğıöşü0-9]{3,}',(s or '').lower()) if x not in {'urun','ürün','fiyat','fiyatları','model','yeni','icin','için','ile','ve'}}
def score(a,b):
    x,y=toks(a),toks(b)
    if not x or not y:return 0
    model={t for t in x if any(c.isdigit() for c in t)}
    return len(x&y)/len(x)+(0.35 if model and model&y else 0)

def search(title):
    url='https://www.akakce.com/arama/?q='+quote(' '.join(title.split())[:120])
    r=requests.get(url,headers=HEAD,timeout=12)
    if not r.ok:return None
    soup=BeautifulSoup(r.text,'html.parser');best=None
    # product links generally contain /.../en-ucuz-...-fiyati,...html
    for a in soup.select('a[href]'):
        txt=a.get_text(' ',strip=True)
        href=a.get('href') or ''
        if 'fiyati' not in href and 'en-ucuz' not in href:continue
        sc=score(title,txt+' '+href)
        if sc<0.50:continue
        parent=a.parent
        context=(parent.get_text(' ',strip=True) if parent else txt)[:900]
        m=re.search(r'En\s+Ucuz\s+([\d.,]+)\s*TL',context,re.I) or re.search(r'([\d.,]+)\s*TL',context,re.I)
        p=money(m.group(1)) if m else None
        if not p:continue
        full=urljoin('https://www.akakce.com',href)
        cand=(sc,p,txt.strip() or title,full)
        if best is None or cand[0]>best[0]:best=cand
    return best

def detail(title,url,current_price):
    try:r=requests.get(url,headers=HEAD,timeout=12)
    except:return None,None
    if not r.ok:return None,None
    text=BeautifulSoup(r.text,'html.parser').get_text(' ',strip=True)
    low=now=None
    for pat in [r'Dönem\s+İçi\s+En\s+Düşük\s+Fiyat\s*:?\s*([\d.,]+)\s*TL',r'En\s+Düşük\s+Fiyat\s*:?\s*([\d.,]+)\s*TL']:
        m=re.search(pat,text,re.I)
        if m:low=money(m.group(1));break
    for pat in [r'Şu\s+Anki\s+Fiyat\s*:?\s*([\d.,]+)\s*TL',r'En\s+Ucuz\s+([\d.,]+)\s*TL']:
        m=re.search(pat,text,re.I)
        if m:now=money(m.group(1));break
    return now or current_price,low

def main():
    rows=ls.list_products(LIMIT*3);done=stored=0
    print(f'=== AKAKÇE ÜCRETSİZ ZENGİNLEŞTİRME | limit={LIMIT} ===')
    for row in rows:
        if done>=LIMIT:break
        title=row.get('title') or ''
        if len(title)<5:continue
        done+=1
        try:
            hit=search(title)
            if not hit:
                print(f'AKAKCE BULAMADI | {title[:65]}');time.sleep(SLEEP);continue
            sc,p,matched,url=hit;now,low=detail(title,url,p);dt=datetime.now(timezone.utc).isoformat()
            ar.add(title,now,row.get('site') or '',None,'Akakce','comparison-current',url,dt);stored+=1
            if low and low>0:ar.add(title,low,row.get('site') or '',None,'Akakce','comparison-history-low',url,dt);stored+=1
            print(f'AKAKCE | skor={sc:.2f} | şimdi={now:.2f} | 6ay_dip={low or 0:.2f} | {title[:60]}')
        except Exception as e:print(f'AKAKCE HATA | {type(e).__name__}: {e}')
        time.sleep(SLEEP)
    print(f'=== AKAKCE BİTTİ | kontrol={done} | kayıt={stored} | arsiv={ar.stats()} ===')

if __name__=='__main__':main()
