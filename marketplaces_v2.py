import os,re,html as htmlmod,base64,requests
from datetime import datetime,timezone,timedelta
from urllib.parse import urlparse,parse_qs,unquote,quote,urljoin
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

TOKEN=os.environ['TELEGRAM_BOT_TOKEN']; SUPABASE_URL=os.environ['SUPABASE_URL'].rstrip('/'); SUPABASE_KEY=os.environ['SUPABASE_SERVICE_KEY']
CHANNEL_ID='-1004424116637'; MIN_DISCOUNT=10.0; COOLDOWN=12; HISTORY_DAYS=90; MAX_PRODUCTS=10
HEADERS={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36','Accept-Language':'tr-TR,tr;q=0.9,en;q=0.8'}
SITES={'Hepsiburada':'hepsiburada.com','Trendyol':'trendyol.com'}
TERMS=['indirim','fırsat','kampanya','telefon','laptop','kulaklık','televizyon','elektronik','oyuncu','ev yaşam']

def sb(method,path,**kwargs):
    h={'apikey':SUPABASE_KEY,'Authorization':f'Bearer {SUPABASE_KEY}','Content-Type':'application/json','Accept':'application/json'}
    if method.upper()=='POST': h['Prefer']='return=representation'
    r=requests.request(method,f'{SUPABASE_URL}/rest/v1/{path}',headers=h,timeout=12,**kwargs); r.raise_for_status(); return r.json() if r.text else []

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
        x=float(s); return x if 0<x<10000000 else None
    except:return None

def prices(text):
    out=[]
    for m in re.finditer(r'(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)',text or '',re.I):
        x=price(m.group(1));
        if x is not None: out.append(x)
    return sorted(set(out))

def unwrap(u):
    u=htmlmod.unescape(unquote(str(u or ''))).replace('\\/','/').replace('\\u002F','/')
    if u.startswith('//'):u='https:'+u
    for _ in range(10):
        p=urlparse(u); q=parse_qs(p.query); nxt=None
        for k in ('q','url','uddg','target','dest','destination'):
            if q.get(k) and q[k][0].startswith(('http://','https://')): nxt=unquote(q[k][0]); break
        if not nxt and 'bing.com' in p.netloc.lower() and q.get('u'):
            raw=q['u'][0]
            if raw.startswith('a1'):
                try:nxt=base64.urlsafe_b64decode(raw[2:]+'='*((4-len(raw[2:])%4)%4)).decode('utf-8','ignore')
                except:pass
        if not nxt: break
        u=nxt
    return u

def marketplace_url(site,raw,base=''):
    u=unwrap(raw); u=urljoin(base,u); p=urlparse(u); domain=SITES[site]
    if domain not in p.netloc.lower(): return None
    if site=='Trendyol' and not re.search(r'-p-\d+(?:[/?#]|$)',p.path,re.I): return None
    if site=='Hepsiburada' and not re.search(r'-p-[A-Za-z0-9]+(?:[/?#]|$)',p.path,re.I): return None
    return f'https://www.{domain}{p.path.rstrip("/")}'

def add(site,raw,title,text,out,seen):
    u=marketplace_url(site,raw)
    if not u or u in seen:return
    seen.add(u); ps=prices(text); out.append((u,re.sub(r'\s+',' ',title or 'Ürün').strip()[:300],ps))
    print(f'{site} aday: {title[:100] if title else "Ürün"} | snippet fiyatları={ps[:8]} | {u}')

def extract_search(site,html,base):
    raw=htmlmod.unescape(html).replace('\\/','/').replace('\\u002F','/')
    soup=BeautifulSoup(raw,'html.parser'); out=[]; seen=set()
    blocks=soup.select('li.b_algo, div.MjjYud, div.g, div.yuRUbf, .result, .web-result, .algo, article')
    if not blocks: blocks=soup.find_all('a',href=True)
    for block in blocks:
        text=re.sub(r'\s+',' ',block.get_text(' ',strip=True))
        for a in block.find_all('a',href=True):
            add(site,a.get('href'),a.get_text(' ',strip=True) or text[:180],text,out,seen)
            if len(out)>=MAX_PRODUCTS:return out
    # HTML içindeki çıplak/escape edilmiş ürün URL'leri de tara.
    pats=[r'https?://(?:www\.)?'+re.escape(SITES[site])+r'/[^"\'<>\s]+-p-[A-Za-z0-9]+',r'/(?:[^"\'<>\s]+)-p-[A-Za-z0-9]+']
    for pat in pats:
        for href in re.findall(pat,raw,re.I):
            add(site,href,'Arama sonucu',raw,out,seen)
            if len(out)>=MAX_PRODUCTS:return out
    return out

def engine_search(site,term,engine):
    domain=SITES[site]; query=f'site:{domain} {term} (TL OR ₺)'; q=quote(query)
    urls={'bing':f'https://www.bing.com/search?q={q}&count=30','google':f'https://www.google.com/search?q={q}&num=30&filter=0','yahoo':f'https://search.yahoo.com/search?p={q}&n=30','duck':f'https://html.duckduckgo.com/html/?q={q}'}
    try:
        r=requests.get(urls[engine],headers=HEADERS,timeout=15,allow_redirects=True); print(f'{site} {engine} [{term}] HTTP: {r.status_code}')
        if r.status_code>=400:return []
        got=extract_search(site,r.text,urls[engine]); print(f'{site} {engine} [{term}] aday: {len(got)}'); return got
    except Exception as e: print(f'{site} {engine} hata: {type(e).__name__}: {e}'); return []

def direct_search(site,term,browser):
    url=('https://www.hepsiburada.com/ara?q=' if site=='Hepsiburada' else 'https://www.trendyol.com/sr?q=')+quote(term)
    page=browser.new_page(); page.set_default_timeout(5000); page.set_default_navigation_timeout(15000); out=[]; seen=set()
    try:
        r=page.goto(url,wait_until='domcontentloaded'); status=r.status if r else 0; print(f'{site} direkt [{term}] HTTP: {status}')
        if not r or status>=400:return []
        page.wait_for_timeout(1800); html=page.content(); out=extract_search(site,html,url)
        if not out:
            # JS ile üretilen sayfada tüm href'leri ayrıca oku.
            for a in page.locator('a[href]').all():
                try:add(site,a.get_attribute('href'),a.inner_text(timeout=200),a.inner_text(timeout=200),out,seen)
                except:pass
                if len(out)>=MAX_PRODUCTS:break
        print(f'{site} direkt [{term}] aday: {len(out)}'); return out
    except Exception as e: print(f'{site} direkt hata: {type(e).__name__}: {e}'); return []
    finally: page.close()

def discover(site,browser):
    found=[];seen=set()
    # Önce gerçek marketplace arama sayfası; arama motorları sadece yedek.
    for term in TERMS:
        for item in direct_search(site,term,browser):
            if item[0] not in seen:seen.add(item[0]);found.append(item)
            if len(found)>=MAX_PRODUCTS:return found
    for term in TERMS:
        for engine in ('bing','google','yahoo','duck'):
            for item in engine_search(site,term,engine):
                if item[0] not in seen:seen.add(item[0]);found.append(item)
                if len(found)>=MAX_PRODUCTS:return found
    return found

def jsonld(html):
    vals=[];name=None
    for s in BeautifulSoup(html,'html.parser').find_all('script',type='application/ld+json'):
        try:
            x=s.string or s.get_text(); obj=__import__('json').loads(x); stack=obj if isinstance(obj,list) else [obj]
            for o in stack:
                if not isinstance(o,dict):continue
                typ=o.get('@type'); types=typ if isinstance(typ,list) else [typ]
                if 'Product' not in types:continue
                name=name or o.get('name'); off=o.get('offers') or {}; off=off if isinstance(off,list) else [off]
                for z in off:
                    if isinstance(z,dict):
                        for k in ('price','lowPrice','highPrice'):
                            x=price(z.get(k));
                            if x is not None:vals.append(x)
        except:pass
    return name,sorted(set(vals))

def product_page(site,url,title,search_prices,browser):
    page=browser.new_page(); page.set_default_timeout(5000); page.set_default_navigation_timeout(15000)
    try:
        r=page.goto(url,wait_until='domcontentloaded'); status=r.status if r else 0; print(f'{site} ürün HTTP: {status} | {url}')
        if not r or status>=400:
            ps=sorted(set(search_prices or [])); cur=ps[0] if ps else None; prev=next((x for x in ps if cur and x>cur*1.03),None)
            if cur:
                print(f'{site} ürün engelli; arama sonucu fiyatı kullanılıyor: {ps[:8]}')
                return {'url':url,'title':title,'current':cur,'previous':prev}
            return None
        page.wait_for_timeout(1200); html=page.content(); name,jd=jsonld(html)
        if site=='Trendyol':
            sels=['meta[property="product:price:amount"]','meta[itemprop="price"]','[data-testid="price-current"]','[class*="prc-dsc"]','[class*="price-current"]']
            olds=['[class*="prc-org"]','[class*="price-original"]','[class*="original-price"]','[class*="strike"]']
        else:
            sels=['meta[property="product:price:amount"]','meta[itemprop="price"]','[data-test-id="price-current"]','[data-test-id="current-price"]','[class*="product-price"]','[class*="current-price"]']
            olds=['[class*="old-price"]','[class*="original-price"]','[class*="strike"]','[class*="previous-price"]']
        def first(selectors):
            for sel in selectors:
                try:
                    loc=page.locator(sel)
                    for i in range(min(loc.count(),10)):
                        raw=loc.nth(i).get_attribute('content') if sel.startswith('meta') else loc.nth(i).inner_text(timeout=300); x=price(raw)
                        if x is not None:return x
                except:pass
            return None
        cur=first(sels)
        if cur is None and jd:cur=jd[0]
        old=first(olds)
        if cur is None:
            vals2=prices(page.locator('body').inner_text(timeout=4000)); cur=vals2[0] if vals2 else None
        if cur is None:return None
        prev=old if old and old>cur*1.03 else None
        if prev is None:prev=next((x for x in jd if x>cur*1.03),None)
        name=name or title or 'Ürün'
        try:name=page.locator('meta[property="og:title"]').get_attribute('content') or name
        except:pass
        print(f'{site} güvenilir fiyat: {cur:.2f} | önceki={prev or 0:.2f} | JSON-LD={jd[:8]}')
        return {'url':url,'title':re.sub(r'\s+',' ',name).strip()[:300],'current':cur,'previous':prev}
    except Exception as e: print(f'{site} ürün hata: {type(e).__name__}: {e}'); return None
    finally: page.close()

def history(url):
    since=(datetime.now(timezone.utc)-timedelta(days=HISTORY_DAYS)).isoformat()
    try:
        rows=sb('GET','price_history',params={'select':'price,recorded_at','product_url':f'eq.{url}','recorded_at':f'gte.{since}','order':'recorded_at.desc','limit':'100'})
        return [float(x['price']) for x in rows if x.get('price') is not None]
    except:return []

def process(p):
    try:
        now=datetime.now(timezone.utc).isoformat(); rows=sb('GET','products',params={'select':'*','product_url':f'eq.{p["url"]}','limit':'1'}); row=rows[0] if rows else None
        old=history(p['url']); previous_observed=old[0] if old else None
        base=previous_observed
        payload={'product_name':p['title'],'current_price':p['current'],'previous_price':previous_observed,'product_url':p['url'],'site':p['site'],'updated_at':now}
        if row:sb('PATCH',f'products?id=eq.{row["id"]}',json=payload)
        else:row=(sb('POST','products',json=payload) or [payload])[0]
        sb('POST','price_history',json={'price':p['current'],'product_url':p['url'],'site':p['site'],'recorded_at':now})
        print(f'Kontrol: {p["site"]} | mevcut={p["current"]:.2f} | son gözlenen={base or 0:.2f} | geçmiş={len(old)}')
        if not base or base<=p['current']:return False
        disc=(base-p['current'])/base*100
        if disc<MIN_DISCOUNT:return False
        last=row.get('last_posted_at') if isinstance(row,dict) else None
        if last:
            try:
                if datetime.now(timezone.utc)-datetime.fromisoformat(last.replace('Z','+00:00'))<timedelta(hours=COOLDOWN):return False
            except:pass
        msg=f'🔥 %{disc:.0f} İNDİRİM\n\n{p["title"]}\n\n💰 {p["current"]:,.2f} TL\n🏷️ Önce: {base:,.2f} TL\n🛍️ {p["site"]}\n🔗 {p["url"]}'
        r=requests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage',json={'chat_id':CHANNEL_ID,'text':msg},timeout=10); print(f'Telegram {p["site"]}: {r.status_code}')
        if r.ok and isinstance(row,dict) and row.get('id'):sb('PATCH',f'products?id=eq.{row["id"]}',json={'last_posted_at':now,'last_posted_price':p['current']})
        return r.ok
    except Exception as e:print(f'işlem hata: {type(e).__name__}: {e}');return False

def main():
    print('=== HB/Trendyol V2 keşif başladı ==='); sent=0
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox','--disable-blink-features=AutomationControlled'])
        for site in SITES:
            items=discover(site,browser); print(f'{site}: {len(items)} aday')
            for url,title,search_prices in items:
                data=product_page(site,url,title,search_prices,browser)
                if data:
                    data['site']=site
                    if process(data):sent+=1
        browser.close()
    print(f'=== V2 bitti. Gönderilen: {sent} ===')

if __name__=='__main__':main()
