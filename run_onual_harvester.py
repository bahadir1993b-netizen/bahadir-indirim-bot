import os,re,time,requests
from datetime import datetime,timezone
from bs4 import BeautifulSoup
import archive_store as ar

PAGES=max(1,int(os.environ.get('ONUAL_PAGES_PER_RUN','30')))
WORKERS=max(1,min(8,int(os.environ.get('ONUAL_WORKERS','4'))))
HEAD={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36','Accept-Language':'tr-TR,tr;q=0.9','Cache-Control':'no-cache'}
CATS={1:'Amazon',2:'Hepsiburada',3:'Trendyol'}

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

def parse_cards(html,site):
    soup=BeautifulSoup(html,'html.parser');out=[]
    # Work with headings and nearby text; OnuAl markup changes periodically.
    for h in soup.find_all(['h2','h3','h4']):
        title=h.get_text(' ',strip=True)
        if len(title)<5 or title.lower() in {'amazon ürünleri','hepsiburada ürünleri','trendyol ürünleri'}:continue
        parent=h.parent
        text=(parent.get_text(' ',strip=True) if parent else '')[:1200]
        m=re.search(r'(\d[\d.,]*)\s*TL\b',text,re.I)
        price=money(m.group(1)) if m else None
        if not price:continue
        href=''
        a=(parent.find('a',href=True) if parent else None) or h.find('a',href=True)
        if a:href=a.get('href') or ''
        out.append((title,price,href))
    # fallback for list/card containers
    if not out:
        for box in soup.select('article,.card,.product,.urun,.item,li'):
            text=box.get_text(' ',strip=True)
            m=re.search(r'(\d[\d.,]*)\s*TL\b',text,re.I)
            if not m:continue
            price=money(m.group(1));title=(box.find(['h2','h3','h4']) or box.find('a'))
            title=title.get_text(' ',strip=True) if title else ''
            if price and len(title)>=5:out.append((title,price,''))
    seen=set();ded=[]
    for x in out:
        k=(x[0].lower(),x[1])
        if k not in seen:seen.add(k);ded.append(x)
    return ded

def harvest_cat(cat,site):
    cursor=int(ar.cursor_get('onual-'+str(cat)) or 1);saved=0;scanned=0
    for page in range(cursor,cursor+PAGES):
        url=f'https://onual.com/fiyat/kategori.php?kat={cat}&sayfa={page}'
        try:r=requests.get(url,headers=HEAD,timeout=15)
        except Exception as e:
            print(f'ONUAL {site} sayfa={page} hata={type(e).__name__}:{e}');break
        if r.status_code==403:
            print(f'ONUAL {site} sayfa={page} HTTP403 - bu tur atlandı');break
        if not r.ok:
            print(f'ONUAL {site} sayfa={page} HTTP{r.status_code}');break
        cards=parse_cards(r.text,site)
        if not cards:
            print(f'ONUAL {site} sayfa={page} kayıt=0');break
        now=datetime.now(timezone.utc).isoformat()
        for title,price,href in cards:
            scanned+=1
            if ar.add(title,price,site,None,'OnuAl-Archive','archive-current',href,now):saved+=1
        ar.cursor_set('onual-'+str(cat),page+1)
        print(f'ONUAL {site} sayfa={page} bulunan={len(cards)} toplam_kayit={saved}')
        time.sleep(.2)
    return scanned,saved

def main():
    print(f'=== ONUAL ÜCRETSİZ ARŞİV HASADI | sayfa/kategori={PAGES} ===')
    total=save=0
    for cat,site in CATS.items():
        a,b=harvest_cat(cat,site);total+=a;save+=b
    print(f'=== ONUAL BİTTİ | taranan={total} | kaydedilen={save} | arsiv={ar.stats()} ===')

if __name__=='__main__':main()
