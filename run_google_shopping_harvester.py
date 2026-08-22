import os,re,time
from datetime import datetime,timezone
from urllib.parse import quote
from playwright.sync_api import sync_playwright
import archive_store as ar
import local_store as ls

LIMIT=max(8,int(os.environ.get('GOOGLE_SHOPPING_LIMIT','30')))
SLEEP=max(1.0,float(os.environ.get('GOOGLE_SHOPPING_SLEEP','2.2')))
DISCOVERY_PER_RUN=max(2,int(os.environ.get('GOOGLE_DISCOVERY_QUERIES_PER_RUN','6')))
CURSOR='google-shopping-index-v2'
DISCOVERY_CURSOR='google-shopping-discovery-index-v1'
DISCOVERY_QUERIES=[
 'amazon indirim fırsat','hepsiburada indirim fırsat','trendyol indirim fırsat','n11 indirim fırsat',
 'şampuan indirim','kişisel bakım indirim','deterjan temizlik indirim','bebek bezi indirim','ıslak mendil indirim',
 'kahve indirim','market gıda indirim','mutfak ürünleri indirim','küçük ev aletleri indirim',
 'kulaklık indirim','akıllı saat indirim','telefon aksesuar indirim','tablet indirim','televizyon indirim',
 'oyuncak indirim','kırtasiye indirim','ev yaşam indirim','spor ayakkabı indirim'
]
BAD={'fırsat','firsat','indirim','kampanya','amazon','hepsiburada','trendyol','n11','sepette','kupon','kargo','bedava','ürün','urun','satıcı','satici'}

def clean(s):
    s=re.sub(r'https?://\S+',' ',str(s or ''))
    s=re.sub(r'[🔥⭐️🛍️💰🏷️👇👉📣🎯✅🚨💥🔗🎁]+',' ',s)
    s=re.sub(r'(?i)fırsata\s*git|fir[sş]ata\s*git|sepette|kupon|kargo\s*bedava',' ',s)
    s=re.sub(r'\s+',' ',s).strip(' -|')
    return s[:180]

def toks(s):return {x for x in re.findall(r'[a-zçğıöşü0-9]{3,}',clean(s).lower()) if x not in BAD}
def sim(a,b):
    x,y=toks(a),toks(b)
    if not x or not y:return 0
    models={t for t in x if any(c.isdigit() for c in t)}
    return len(x&y)/len(x)+(0.45 if models and models&y else 0)

def money(s):
    m=re.search(r'([\d.]+(?:,\d{1,2})?)\s*TL',str(s or ''),re.I)
    if not m:return None
    v=m.group(1)
    if ',' in v:v=v.replace('.','').replace(',','.')
    elif v.count('.')>1:v=v.replace('.','')
    else:
        a=v.rsplit('.',1)
        if len(a)==2 and len(a[1])==3:v=v.replace('.','')
    try:
        p=float(v);return p if 1<p<10000000 else None
    except:return None

def candidates():
    seen=set();out=[]
    for r in ls.list_products(100000):
        t=clean(r.get('title') or '');k=ar.key(t)
        if len(t)>=6 and k and k not in seen:
            seen.add(k);out.append({'title':t,'known':r.get('last_price')})
    for r in ar.list_title_candidates(100000):
        t=clean(r.get('title') or '');k=ar.key(t)
        if len(t)>=6 and k and k not in seen:
            seen.add(k);out.append({'title':t,'known':r.get('price')})
    return out

def _nodes(page):
    selectors=['div.sh-dgr__grid-result','div[data-docid]','div.sh-dgr__content','div[jsname="ZvZkAe"]']
    for sel in selectors:
        try:
            loc=page.locator(sel);cnt=min(loc.count(),80)
            if cnt:return [loc.nth(i) for i in range(cnt)]
        except:pass
    try:
        loc=page.locator('body div');return [loc.nth(i) for i in range(min(loc.count(),300))]
    except:return []

def _name_from_text(txt):
    lines=[clean(x) for x in re.split(r'[\n\r]+',txt or '') if clean(x)]
    for line in lines[:8]:
        if 'TL' in line:continue
        if len(line)>=8 and len(toks(line))>=2:return line[:180]
    before=(txt or '').split(' TL')[0]
    before=re.sub(r'\b[\d.]+(?:,\d{1,2})?$','',before).strip(' -|')
    return clean(before)

def extract(page,title,known=None):
    hits=[]
    for node in _nodes(page):
        try:txt=re.sub(r'\s+',' ',node.inner_text(timeout=800)).strip()
        except:continue
        if len(txt)<8 or 'TL' not in txt:continue
        p=money(txt)
        if not p:continue
        name=_name_from_text(txt) or title
        sc=sim(title,name or txt)
        if sc<0.48:continue
        try:k=float(known) if known else None
        except:k=None
        if k and not (k*0.35<=p<=k*2.8):continue
        hits.append((sc,p,name))
    uniq=[];seen=set()
    for h in sorted(hits,key=lambda x:x[0],reverse=True):
        key=(round(h[1],2),ar.key(h[2]))
        if key in seen:continue
        seen.add(key);uniq.append(h)
    return uniq[:8]

def extract_discovery(page):
    hits=[];seen=set()
    for node in _nodes(page):
        try:txt=node.inner_text(timeout=800)
        except:continue
        if not txt or 'TL' not in txt:continue
        p=money(txt);name=_name_from_text(txt)
        if not p or len(name)<8 or len(toks(name))<2:continue
        key=(ar.key(name),round(p,2))
        if key in seen:continue
        seen.add(key);hits.append((p,name))
    return hits[:20]

def _open(page,q):
    url=f'https://www.google.com/search?tbm=shop&hl=tr&gl=tr&q={quote(q[:140])}'
    page.goto(url,wait_until='domcontentloaded',timeout=18000);page.wait_for_timeout(900)
    body=(page.locator('body').inner_text(timeout=2500) or '').lower()
    if 'olağandışı trafik' in body or 'unusual traffic' in body or 'captcha' in body:return False
    return True

def main():
    rows=candidates()
    try:start=int(ar.cursor_get(CURSOR) or 0)
    except:start=0
    try:dstart=int(ar.cursor_get(DISCOVERY_CURSOR) or 0)
    except:dstart=0
    n=len(rows);catalog_budget=max(0,LIMIT-DISCOVERY_PER_RUN)
    picked=[rows[(start+i)%n] for i in range(min(catalog_budget,n))] if n else []
    dqs=[DISCOVERY_QUERIES[(dstart+i)%len(DISCOVERY_QUERIES)] for i in range(min(DISCOVERY_PER_RUN,len(DISCOVERY_QUERIES)))]
    checked=stored=blocked=discovery_saved=0
    print(f'=== GOOGLE SHOPPING ÜCRETSİZ HASAT V2 | katalog_aday={n} | katalog_tur={len(picked)} | keşif_sorgu={len(dqs)} ===')
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--disable-blink-features=AutomationControlled'])
        page=browser.new_page(locale='tr-TR',user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36')
        for qtext in dqs:
            try:
                if not _open(page,qtext):blocked+=1;print('GOOGLE BLOK/CAPTCHA - tur güvenli şekilde durduruldu');break
                dt=datetime.now(timezone.utc).isoformat();hits=extract_discovery(page)
                for p,name in hits:
                    if ar.add(name,p,'',None,'GoogleShoppingDiscovery','discovery-current','',dt):discovery_saved+=1
                print(f'GOOGLE KEŞİF | sorgu={qtext} | ürün={len(hits)} | kayıt={discovery_saved}')
            except Exception as e:print(f'GOOGLE KEŞİF HATA | {type(e).__name__}: {str(e)[:100]}')
            time.sleep(SLEEP)
        if not blocked:
            for row in picked:
                title=row['title'];checked+=1
                try:
                    if not _open(page,' '.join(title.split())[:120]):blocked+=1;print('GOOGLE BLOK/CAPTCHA - tur güvenli şekilde durduruldu');break
                    hits=extract(page,title,row.get('known'))
                    if not hits:print(f'GOOGLE BULAMADI | {title[:72]}')
                    else:
                        dt=datetime.now(timezone.utc).isoformat()
                        for sc,p,name in hits:
                            ar.add(title,p,'',None,'GoogleShopping','comparison-current','',dt);stored+=1
                        prices=','.join(f'{x[1]:.0f}' for x in hits[:5])
                        print(f'GOOGLE | eşleşme={hits[0][0]:.2f} | fiyatlar={prices} | {title[:60]}')
                except Exception as e:print(f'GOOGLE HATA | {type(e).__name__}: {str(e)[:100]}')
                time.sleep(SLEEP)
        browser.close()
    if n:ar.cursor_set(CURSOR,(start+checked)%n)
    ar.cursor_set(DISCOVERY_CURSOR,(dstart+len(dqs))%len(DISCOVERY_QUERIES))
    print(f'=== GOOGLE BİTTİ | katalog_kontrol={checked} | karşılaştırma_kayıt={stored} | yeni_keşif_kayıt={discovery_saved} | blok={blocked} | arsiv={ar.stats()} ===')

if __name__=='__main__':main()
