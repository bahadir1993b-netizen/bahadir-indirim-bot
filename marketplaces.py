import os,re,json,requests
from datetime import datetime,timezone,timedelta
from urllib.parse import urlparse,parse_qs,unquote,urljoin
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

TOKEN=os.environ['TELEGRAM_BOT_TOKEN']
SUPABASE_URL=os.environ['SUPABASE_URL'].rstrip('/')
SUPABASE_KEY=os.environ['SUPABASE_SERVICE_KEY']
CHANNEL_ID='-1004424116637'
MIN_DISCOUNT=10.0; COOLDOWN=12; HISTORY_DAYS=90; MAX_PRODUCTS=6
HEADERS={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36','Accept-Language':'tr-TR,tr;q=0.9,en;q=0.8'}
SITES={'Hepsiburada':'hepsiburada.com','Trendyol':'trendyol.com'}
TERMS=['indirim','kampanya','fırsat','elektronik','telefon','laptop','kulaklık','televizyon','oyuncu']


def sb(method,path,**kwargs):
    h={'apikey':SUPABASE_KEY,'Authorization':f'Bearer {SUPABASE_KEY}','Content-Type':'application/json','Accept':'application/json'}
    if method.upper()=='POST': h['Prefer']='return=representation'
    r=requests.request(method,f'{SUPABASE_URL}/rest/v1/{path}',headers=h,timeout=12,**kwargs); r.raise_for_status()
    return r.json() if r.text else []


def price(v):
    if v is None:return None
    s=re.sub(r'[^0-9,.]','',str(v).replace('TL','').replace('₺','').replace(' ',''))
    if not s:return None
    if ',' in s and '.' in s: s=s.replace('.','').replace(',','.') if s.rfind(',')>s.rfind('.') else s.replace(',','')
    elif ',' in s:
        a,b=s.rsplit(',',1); s=a.replace('.','')+'.'+b if len(b)<=2 else s.replace(',','')
    elif '.' in s:
        a,b=s.rsplit('.',1); s=s.replace('.','') if len(b)>2 else s
    try:
        v=float(s); return v if 1<=v<10000000 else None
    except:return None


def prices(text):
    out=[]
    for m in re.finditer(r'(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)',text or '',re.I):
        v=price(m.group(1))
        if v is not None: out.append(v)
    return sorted(set(out))


def unwrap(u):
    u=unquote(str(u or '')).replace('\\/','/')
    for _ in range(5):
        q=parse_qs(urlparse(u).query); nxt=None
        for k in ('q','url','u','uddg','target'):
            if q.get(k) and q[k][0].startswith(('http://','https://')): nxt=q[k][0]; break
        if not nxt: break
        u=unquote(nxt)
    return u


def product_url(site,href):
    u=unwrap(href)
    if not u.startswith(('http://','https://')): return None
    p=urlparse(u); domain=SITES[site]
    if domain not in p.netloc.lower(): return None
    if not re.search(r'-p-[a-z0-9]+(?:/|$)',p.path,re.I): return None
    return f'https://{p.netloc.lower()}{p.path.rstrip("/")}'


def add_candidate(site,href,title,text,out,seen):
    u=product_url(site,href)
    if not u or u in seen:return
    ps=prices(text)
    if not ps:return
    seen.add(u); out.append((u,(title or text[:220]).strip()[:250],ps))
    print(f'{site} aday: {(title or text[:80]).strip()[:80]} | fiyatlar={ps[:5]} | {u}')


def search_engine(site,term,engine):
    domain=SITES[site]; q=requests.utils.quote(f'site:{domain} {term} TL')
    if engine=='google': url=f'https://www.google.com/search?q={q}&num=20'
    elif engine=='bing': url=f'https://www.bing.com/search?q={q}&count=20'
    else: url=f'https://html.duckduckgo.com/html/?q={q}'
    out=[];seen=set()
    try:
        r=requests.get(url,headers=HEADERS,timeout=15,allow_redirects=True)
        print(f'{site} {engine} [{term}] HTTP: {r.status_code}')
        if r.status_code>=400:return out
        soup=BeautifulSoup(r.text,'html.parser')
        # Parse result containers first so title + price stay together.
        blocks=soup.select('div.MjjYud, li.b_algo, .result, .results_links, article')
        for block in blocks:
            text=re.sub(r'\s+',' ',block.get_text(' ',strip=True))
            for a in block.find_all('a',href=True):
                add_candidate(site,a.get('href'),a.get_text(' ',strip=True),text,out,seen)
                if len(out)>=MAX_PRODUCTS:return out
        # Fallback: scan every anchor and nearby parent text.
        for a in soup.find_all('a',href=True):
            parent=a.parent
            text=re.sub(r'\s+',' ',parent.get_text(' ',strip=True)) if parent else ''
            if not re.search(r'(?:TL|₺)',text,re.I) and parent and parent.parent:
                text=re.sub(r'\s+',' ',parent.parent.get_text(' ',strip=True))
            add_candidate(site,a.get('href'),a.get_text(' ',strip=True),text,out,seen)
            if len(out)>=MAX_PRODUCTS:return out
    except Exception as e: print(f'{site} {engine} hata: {type(e).__name__}: {e}')
    return out


def discover(site):
    found=[];seen=set()
    for term in TERMS:
        for engine in ('google','bing','ddg'):
            for item in search_engine(site,term,engine):
                if item[0] not in seen:
                    seen.add(item[0]);found.append(item)
                if len(found)>=MAX_PRODUCTS:return found
    return found


def jsonld_prices(html):
    vals=[];name=None
    for s in BeautifulSoup(html,'html.parser').find_all('script',type='application/ld+json'):
        try:
            x=json.loads(s.string or s.get_text()); stack=x if isinstance(x,list) else [x]
            for o in stack:
                if not isinstance(o,dict):continue
                typ=o.get('@type'); types=typ if isinstance(typ,list) else [typ]
                if 'Product' not in types:continue
                name=name or o.get('name'); offers=o.get('offers') or {}; offers=offers if isinstance(offers,list) else [offers]
                for off in offers:
                    if isinstance(off,dict):
                        for k in ('price','lowPrice','highPrice'):
                            v=price(off.get(k));
                            if v is not None: vals.append(v)
        except:pass
    return name,vals


def product_page(site,url,title,browser):
    page=browser.new_page(); page.set_default_timeout(5000); page.set_default_navigation_timeout(18000)
    try:
        r=page.goto(url,wait_until='domcontentloaded'); status=r.status if r else 0
        print(f'{site} ürün HTTP: {status} | {url}')
        if not r or status>=400:return None
        page.wait_for_timeout(1200); html=page.content(); text=page.locator('body').inner_text(timeout=5000)
        name,jd=jsonld_prices(html); vals=list(jd)
        selectors=[
            '[data-test-id*="price"]','[data-test*="price"]','[class*="price"]',
            'meta[property="product:price:amount"]','meta[itemprop="price"]'
        ]
        for sel in selectors:
            try:
                loc=page.locator(sel); n=min(loc.count(),30)
                for i in range(n):
                    raw=loc.nth(i).get_attribute('content') if sel.startswith('meta') else loc.nth(i).inner_text(timeout=300)
                    vals.extend(prices(raw) if re.search(r'(?:TL|₺)',str(raw),re.I) else ([price(raw)] if price(raw) is not None else []))
            except:pass
        vals.extend(prices(text)); vals=sorted(set(v for v in vals if v is not None))
        if not vals:
            print(f'{site} ürün fiyat bulunamadı | {url}'); return None
        # Prefer explicit current-price selectors / JSON-LD. Do not simply trust the minimum
        # when a page contains shipping/installment/discount fragments.
        current=None
        for sel in ['[data-test-id="price-current"]','[data-test-id="current-price"]','[data-test="currentPrice"]']:
            try:
                loc=page.locator(sel)
                if loc.count():
                    current=price(loc.first.inner_text(timeout=500));
                    if current is not None:break
            except:pass
        if current is None and jd: current=jd[0]
        if current is None: current=vals[0]
        previous=next((v for v in vals if v>current*1.03),None)
        name=name or title or 'Ürün'
        try:name=page.locator('meta[property="og:title"]').get_attribute('content') or name
        except:pass
        print(f'{site} güvenilir fiyatlar: current={current:.2f} previous={previous or 0:.2f} tüm={vals[:10]}')
        return {'url':url,'title':re.sub(r'\s+',' ',name).strip()[:300],'current':current,'previous':previous}
    except Exception as e: print(f'{site} ürün hata: {type(e).__name__}: {e}'); return None
    finally: page.close()


def history(url):
    since=(datetime.now(timezone.utc)-timedelta(days=HISTORY_DAYS)).isoformat()
    rows=sb('GET','price_history',params={'select':'price,recorded_at','product_url':f'eq.{url}','recorded_at':f'gte.{since}','order':'recorded_at.desc','limit':'100'})
    return [float(x['price']) for x in rows if x.get('price') is not None]


def process(site,p):
    url,title,_=p; data=None
    # Search snippets are discovery only; Telegram decisions use the product page price.
    with sync_playwright() as _p:
        pass
    return False


def save_and_send(site,data):
    url=data['url']; current=data['current']; previous=data.get('previous')
    rows=sb('GET','products',params={'select':'*','product_url':f'eq.{url}','limit':'1'}); row=rows[0] if rows else None
    old=history(url); base=previous or (max(old) if old else None); now=datetime.now(timezone.utc).isoformat()
    payload={'product_name':data['title'],'current_price':current,'previous_price':previous,'product_url':url,'site':site,'updated_at':now}
    if row: sb('PATCH',f'products?id=eq.{row["id"]}',json=payload)
    else: row=(sb('POST','products',json=payload) or [payload])[0]
    sb('POST','price_history',json={'price':current,'product_url':url,'site':site,'recorded_at':now})
    print(f'Kontrol: {site} | mevcut={current:.2f} | baz={base or 0:.2f} | geçmiş={len(old)}')
    if not base or base<=current:return False
    disc=(base-current)/base*100
    if disc<MIN_DISCOUNT:return False
    last=row.get('last_posted_at') if isinstance(row,dict) else None
    if last:
        try:
            if datetime.now(timezone.utc)-datetime.fromisoformat(last.replace('Z','+00:00'))<timedelta(hours=COOLDOWN):return False
        except:pass
    msg=f'🔥 %{disc:.0f} İNDİRİM\n\n{data["title"]}\n\n💰 {current:,.2f} TL\n🏷️ Önce: {base:,.2f} TL\n🛍️ {site}\n🔗 {url}'
    r=requests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage',json={'chat_id':CHANNEL_ID,'text':msg},timeout=10)
    print(f'Telegram {site}: {r.status_code}')
    if not r.ok:return False
    if isinstance(row,dict) and row.get('id'):
        sb('PATCH',f'products?id=eq.{row["id"]}',json={'last_posted_at':now,'last_posted_price':current})
    return True


def main():
    print('=== HB/Trendyol güvenilir keşif başladı ==='); sent=0
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox','--disable-blink-features=AutomationControlled'])
        for site in SITES:
            items=discover(site); print(f'{site}: {len(items)} aday')
            for url,title,_ in items:
                try:
                    data=product_page(site,url,title,browser)
                    if data and save_and_send(site,data): sent+=1
                except Exception as e: print(f'{site} işlem hata: {type(e).__name__}: {e}')
        browser.close()
    print(f'=== HB/Trendyol bitti. Gönderilen: {sent} ===')

if __name__=='__main__': main()
