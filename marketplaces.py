import os,re,json,base64,requests,html as htmlmod
from datetime import datetime,timezone,timedelta
from urllib.parse import urlparse,parse_qs,unquote,quote
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

TOKEN=os.environ['TELEGRAM_BOT_TOKEN']; SUPABASE_URL=os.environ['SUPABASE_URL'].rstrip('/'); SUPABASE_KEY=os.environ['SUPABASE_SERVICE_KEY']; CHANNEL_ID='-1004424116637'
MIN_DISCOUNT=10.0; COOLDOWN=12; HISTORY_DAYS=90; MAX_PRODUCTS=12
HEADERS={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36','Accept-Language':'tr-TR,tr;q=0.9,en;q=0.8'}
SITES={'Hepsiburada':'hepsiburada.com','Trendyol':'trendyol.com'}
TERMS=['indirim','kampanya','fırsat','elektronik','telefon','laptop','kulaklık','televizyon','oyuncu','ev yaşam']
SEARCH_URLS={'Hepsiburada':'https://www.hepsiburada.com/ara?q={q}','Trendyol':'https://www.trendyol.com/sr?q={q}'}

def sb(method,path,**kwargs):
    h={'apikey':SUPABASE_KEY,'Authorization':f'Bearer {SUPABASE_KEY}','Content-Type':'application/json','Accept':'application/json'}
    if method.upper()=='POST': h['Prefer']='return=representation'
    r=requests.request(method,f'{SUPABASE_URL}/rest/v1/{path}',headers=h,timeout=12,**kwargs); r.raise_for_status(); return r.json() if r.text else []

def price(v):
    if v is None:return None
    s=re.sub(r'[^0-9,.]','',str(v).replace('TL','').replace('₺','').replace(' ',''))
    if not s:return None
    if ',' in s and '.' in s:s=s.replace('.','').replace(',','.') if s.rfind(',')>s.rfind('.') else s.replace(',','')
    elif ',' in s:
        a,b=s.rsplit(',',1);s=a.replace('.','')+'.'+b if len(b)<=2 else s.replace(',','')
    elif '.' in s:
        a,b=s.rsplit('.',1);s=s.replace('.','') if len(b)>2 else s
    try:
        v=float(s);return v if 1<=v<10000000 else None
    except:return None

def prices(text):
    out=[]
    for m in re.finditer(r'(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)',text or '',re.I):
        v=price(m.group(1))
        if v is not None:out.append(v)
    return sorted(set(out))

def unwrap(u):
    u=htmlmod.unescape(unquote(str(u or ''))).replace('\\/','/').replace('\\u002F','/')
    for _ in range(6):
        q=parse_qs(urlparse(u).query); nxt=None
        for k in ('q','url','uddg','target'):
            if q.get(k) and q[k][0].startswith(('http://','https://')):nxt=unquote(q[k][0]);break
        if not nxt and 'bing.com' in urlparse(u).netloc.lower() and q.get('u'):
            raw=q['u'][0]
            if raw.startswith('a1'):
                try:
                    dec=base64.urlsafe_b64decode(raw[2:]+'='*((4-len(raw[2:])%4)%4)).decode('utf-8','ignore')
                    if dec.startswith(('http://','https://')):nxt=dec
                except Exception:pass
        if not nxt:break
        u=nxt
    return u

def product_url(site,href):
    u=unwrap(href)
    if u.startswith('//'):u='https:'+u
    if not u.startswith(('http://','https://')):return None
    p=urlparse(u); domain=SITES[site]
    if domain not in p.netloc.lower():return None
    pattern=r'-p-\d+(?:[/?#]|$)' if site=='Trendyol' else r'-p-[A-Za-z0-9]+(?:[/?#]|$)'
    if not re.search(pattern,p.path,re.I):return None
    return f'https://{p.netloc.lower()}{p.path.rstrip("/")}'

def add_candidate(site,href,title,text,out,seen):
    u=product_url(site,href)
    if not u or u in seen:return
    ps=prices(text); seen.add(u); out.append((u,(title or 'Ürün').strip()[:250],ps))
    print(f'{site} aday: {(title or "Ürün").strip()[:90]} | arama fiyatları={ps[:5]} | {u}')

def search_engine(site,term,engine):
    domain=SITES[site]; q=quote(f'site:{domain} inurl:-p- {term} TL')
    if engine=='bing':url=f'https://www.bing.com/search?q={q}&count=20'
    elif engine=='yahoo':url=f'https://search.yahoo.com/search?p={q}'
    else:url=f'https://www.google.com/search?q={q}&num=20&filter=0'
    out=[];seen=set()
    try:
        r=requests.get(url,headers=HEADERS,timeout=15,allow_redirects=True); print(f'{site} {engine} [{term}] HTTP: {r.status_code}')
        if r.status_code>=400:return out
        raw=htmlmod.unescape(r.text).replace('\\/','/').replace('\\u002F','/'); soup=BeautifulSoup(raw,'html.parser')
        blocks=soup.select('li.b_algo, div.MjjYud, .result, .web-result, .algo')
        if not blocks:blocks=soup.find_all('a',href=True)
        for block in blocks:
            text=re.sub(r'\s+',' ',block.get_text(' ',strip=True))
            for a in block.find_all('a',href=True):
                add_candidate(site,a.get('href'),a.get_text(' ',strip=True) or text[:180],text,out,seen)
                if len(out)>=MAX_PRODUCTS:return out
        for href in re.findall(r'https?://(?:www\.)?'+re.escape(domain)+r'/[^"\'<>\s]+-p-[A-Za-z0-9]+',raw,re.I):
            add_candidate(site,href,'Arama sonucu',raw,out,seen)
            if len(out)>=MAX_PRODUCTS:return out
    except Exception as e:print(f'{site} {engine} hata: {type(e).__name__}: {e}')
    return out

def direct_search(site,term,browser):
    url=SEARCH_URLS[site].format(q=quote(term))
    out=[];seen=set();page=browser.new_page();page.set_default_timeout(7000);page.set_default_navigation_timeout(20000)
    try:
        r=page.goto(url,wait_until='domcontentloaded'); print(f'{site} direkt arama [{term}] HTTP: {r.status if r else 0} | {url}')
        if not r or r.status>=400:return out
        page.wait_for_timeout(1800)
        html=page.content(); soup=BeautifulSoup(html,'html.parser')
        # Önce gerçek href'leri tara; arama motorundan bağımsız ürün keşfi.
        for a in soup.find_all('a',href=True):
            href=unwrap(a.get('href')); txt=re.sub(r'\s+',' ',a.get_text(' ',strip=True))
            add_candidate(site,href,txt,' '.join([txt,a.get('aria-label','') or '']),out,seen)
            if len(out)>=MAX_PRODUCTS:break
        # Bazı ürün kartları href yerine HTML/JSON içinde kalıyor.
        if len(out)<MAX_PRODUCTS:
            raw=htmlmod.unescape(html).replace('\\/','/').replace('\\u002F','/')
            for href in re.findall(r'https?://(?:www\.)?'+re.escape(SITES[site])+r'/[^"\'<>\s]+-p-[A-Za-z0-9]+',raw,re.I):
                add_candidate(site,href,'Direkt arama sonucu',raw,out,seen)
                if len(out)>=MAX_PRODUCTS:break
    except Exception as e:print(f'{site} direkt arama hata: {type(e).__name__}: {e}')
    finally:page.close()
    return out

def discover(site,browser):
    found=[];seen=set()
    # Asıl keşif artık marketplace'in kendi arama sayfasından yapılıyor.
    for term in TERMS:
        items=direct_search(site,term,browser)
        for item in items:
            if item[0] not in seen:seen.add(item[0]);found.append(item)
            if len(found)>=MAX_PRODUCTS:return found
        print(f'{site} direkt [{term}] toplam aday: {len(found)}')
    # Direkt sayfa boş/engelli ise arama motorlarını yedek olarak kullan.
    if not found:
        for term in TERMS:
            for engine in ('bing','yahoo','google'):
                for item in search_engine(site,term,engine):
                    if item[0] not in seen:seen.add(item[0]);found.append(item)
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
                        v=price(off.get('price'))
                        if v is not None:vals.append(v)
        except:pass
    return name,vals

def first_price(page,selectors):
    for sel in selectors:
        try:
            loc=page.locator(sel)
            for i in range(min(loc.count(),10)):
                raw=loc.nth(i).get_attribute('content') if sel.startswith('meta') else loc.nth(i).inner_text(timeout=350)
                vals=prices(raw)
                if vals:return vals[0]
                v=price(raw)
                if v is not None:return v
        except:pass
    return None

def product_page(site,url,title,browser):
    page=browser.new_page(); page.set_default_timeout(6000); page.set_default_navigation_timeout(18000)
    try:
        r=page.goto(url,wait_until='domcontentloaded'); status=r.status if r else 0; print(f'{site} ürün HTTP: {status} | {url}')
        if not r or status>=400:return None
        page.wait_for_timeout(1500); html=page.content(); text=page.locator('body').inner_text(timeout=5000); name,jd=jsonld_prices(html)
        if site=='Trendyol':
            current=first_price(page,['[data-testid="price-current"]','[data-testid="price"]','[class*="prc-dsc"]','[class*="price-current"]','[class*="current-price"]','meta[property="product:price:amount"]','meta[itemprop="price"]'])
            old=first_price(page,['[class*="prc-org"]','[class*="price-original"]','[class*="original-price"]','[class*="strike"]'])
        else:
            current=first_price(page,['[data-test-id="price-current"]','[data-test-id="current-price"]','[class*="product-price"]','[class*="current-price"]','meta[property="product:price:amount"]','meta[itemprop="price"]'])
            old=first_price(page,['[class*="old-price"]','[class*="original-price"]','[class*="strike"]','[class*="previous-price"]'])
        if current is None and jd:current=jd[0]
        if current is None:
            bp=prices(text); current=bp[0] if bp else None
        if current is None:return None
        previous=old if old and old>current*1.03 else None
        if previous is None:
            higher=[v for v in jd if v>current*1.03]; previous=min(higher) if higher else None
        name=name or title or 'Ürün'
        try:name=page.locator('meta[property="og:title"]').get_attribute('content') or name
        except:pass
        print(f'{site} güvenilir fiyat: current={current:.2f} previous={previous or 0:.2f} | JSON-LD={jd[:8]}')
        return {'url':url,'title':re.sub(r'\s+',' ',name).strip()[:300],'current':current,'previous':previous}
    except Exception as e:print(f'{site} ürün hata: {type(e).__name__}: {e}');return None
    finally:page.close()

def history(url):
    since=(datetime.now(timezone.utc)-timedelta(days=HISTORY_DAYS)).isoformat(); rows=sb('GET','price_history',params={'select':'price,recorded_at','product_url':f'eq.{url}','recorded_at':f'gte.{since}','order':'recorded_at.desc','limit':'100'}); return [float(x['price']) for x in rows if x.get('price') is not None]

def save_and_send(site,data):
    url=data['url']; current=data['current']; previous=data.get('previous'); rows=sb('GET','products',params={'select':'*','product_url':f'eq.{url}','limit':'1'}); row=rows[0] if rows else None; old=history(url); base=previous or (max(old) if old else None); now=datetime.now(timezone.utc).isoformat()
    payload={'product_name':data['title'],'current_price':current,'previous_price':previous,'product_url':url,'site':site,'updated_at':now}
    if row:sb('PATCH',f'products?id=eq.{row["id"]}',json=payload)
    else:row=(sb('POST','products',json=payload) or [payload])[0]
    sb('POST','price_history',json={'price':current,'product_url':url,'site':site,'recorded_at':now}); print(f'Kontrol: {site} | mevcut={current:.2f} | baz={base or 0:.2f} | geçmiş={len(old)}')
    if not base or base<=current:return False
    disc=(base-current)/base*100
    if disc<MIN_DISCOUNT:return False
    last=row.get('last_posted_at') if isinstance(row,dict) else None
    if last:
        try:
            if datetime.now(timezone.utc)-datetime.fromisoformat(last.replace('Z','+00:00'))<timedelta(hours=COOLDOWN):return False
        except:pass
    msg=f'🔥 %{disc:.0f} İNDİRİM\n\n{data["title"]}\n\n💰 {current:,.2f} TL\n🏷️ Önce: {base:,.2f} TL\n🛍️ {site}\n🔗 {url}'
    r=requests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage',json={'chat_id':CHANNEL_ID,'text':msg},timeout=10); print(f'Telegram {site}: {r.status_code}')
    if not r.ok:return False
    if isinstance(row,dict) and row.get('id'):sb('PATCH',f'products?id=eq.{row["id"]}',json={'last_posted_at':now,'last_posted_price':current})
    return True

def main():
    print('=== HB/Trendyol güvenilir keşif başladı ==='); sent=0
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox','--disable-blink-features=AutomationControlled'])
        for site in SITES:
            items=discover(site,browser); print(f'{site}: {len(items)} aday')
            for url,title,_ in items:
                try:
                    data=product_page(site,url,title,browser)
                    if data and save_and_send(site,data):sent+=1
                except Exception as e:print(f'{site} işlem hata: {type(e).__name__}: {e}')
        browser.close()
    print(f'=== HB/Trendyol bitti. Gönderilen: {sent} ===')

if __name__=='__main__':main()
