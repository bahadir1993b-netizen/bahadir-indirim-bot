import os,re,time
from datetime import datetime,timezone
from urllib.parse import quote
from playwright.sync_api import sync_playwright
import archive_store as ar
import local_store as ls

LIMIT=max(5,int(os.environ.get('GOOGLE_SHOPPING_LIMIT','30')))
SLEEP=max(1.0,float(os.environ.get('GOOGLE_SHOPPING_SLEEP','2.2')))
CURSOR='google-shopping-index-v1'

BAD={'fırsat','firsat','indirim','kampanya','amazon','hepsiburada','trendyol','sepette','kupon','kargo','bedava','ürün','urun'}

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

def extract(page,title,known=None):
    hits=[]
    selectors=['div.sh-dgr__grid-result','div[data-docid]','div.sh-dgr__content','div[jsname="ZvZkAe"]']
    nodes=[]
    for sel in selectors:
        try:
            loc=page.locator(sel);cnt=min(loc.count(),60)
            if cnt:
                nodes=[loc.nth(i) for i in range(cnt)];break
        except:pass
    if not nodes:
        try:
            loc=page.locator('body div');cnt=min(loc.count(),250)
            nodes=[loc.nth(i) for i in range(cnt)]
        except:return []
    for node in nodes:
        try:txt=re.sub(r'\s+',' ',node.inner_text(timeout=800)).strip()
        except:continue
        if len(txt)<8 or 'TL' not in txt:continue
        p=money(txt)
        if not p:continue
        first=txt.split(' TL')[0]
        # ürün adı genellikle kartın ilk/ikinci satırlarında; fiyatın öncesindeki metinden türet
        name=re.sub(r'\b[\d.]+(?:,\d{1,2})?$','',first).strip(' -|')
        sc=sim(title,name or txt)
        if sc<0.48:continue
        try:k=float(known) if known else None
        except:k=None
        if k and not (k*0.35<=p<=k*2.8):continue
        hits.append((sc,p,name or title))
    # aynı fiyatı/ismi tekrarlama
    uniq=[];seen=set()
    for h in sorted(hits,key=lambda x:x[0],reverse=True):
        key=(round(h[1],2),ar.key(h[2]))
        if key in seen:continue
        seen.add(key);uniq.append(h)
    return uniq[:8]

def main():
    rows=candidates()
    if not rows:
        print('=== GOOGLE SHOPPING HASAT | aday=0 ===');return
    try:start=int(ar.cursor_get(CURSOR) or 0)
    except:start=0
    n=len(rows);picked=[rows[(start+i)%n] for i in range(min(LIMIT,n))]
    checked=stored=blocked=0
    print(f'=== GOOGLE SHOPPING ÜCRETSİZ HASAT | aday={n} | tur={len(picked)} | başlangıç={start} ===')
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--disable-blink-features=AutomationControlled'])
        page=browser.new_page(locale='tr-TR',user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36')
        for row in picked:
            title=row['title'];checked+=1
            q=quote(' '.join(title.split())[:120])
            url=f'https://www.google.com/search?tbm=shop&hl=tr&gl=tr&q={q}'
            try:
                page.goto(url,wait_until='domcontentloaded',timeout=18000);page.wait_for_timeout(900)
                body=(page.locator('body').inner_text(timeout=2500) or '').lower()
                if 'olağandışı trafik' in body or 'unusual traffic' in body or 'captcha' in body:
                    blocked+=1;print('GOOGLE BLOK/CAPTCHA - tur güvenli şekilde durduruldu');break
                hits=extract(page,title,row.get('known'))
                if not hits:
                    print(f'GOOGLE BULAMADI | {title[:72]}')
                else:
                    dt=datetime.now(timezone.utc).isoformat()
                    for sc,p,name in hits:
                        ar.add(title,p,'',None,'GoogleShopping','comparison-current','',dt);stored+=1
                    prices=','.join(f'{x[1]:.0f}' for x in hits[:5])
                    print(f'GOOGLE | eşleşme={hits[0][0]:.2f} | fiyatlar={prices} | {title[:60]}')
            except Exception as e:print(f'GOOGLE HATA | {type(e).__name__}: {str(e)[:100]}')
            time.sleep(SLEEP)
        browser.close()
    nxt=(start+checked)%n;ar.cursor_set(CURSOR,nxt)
    print(f'=== GOOGLE BİTTİ | kontrol={checked} | kayıt={stored} | blok={blocked} | sonraki={nxt}/{n} | arsiv={ar.stats()} ===')

if __name__=='__main__':main()
