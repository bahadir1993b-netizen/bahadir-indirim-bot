import os,re,json,warnings
from datetime import datetime,timezone,timedelta
from urllib.parse import urljoin,urlparse,parse_qs,unquote,quote
import requests
from bs4 import BeautifulSoup,XMLParsedAsHTMLWarning
from playwright.sync_api import sync_playwright
warnings.filterwarnings('ignore',category=XMLParsedAsHTMLWarning)

TOKEN=os.environ['TELEGRAM_BOT_TOKEN']; CHANNEL_ID='-1004424116637'
SUPABASE_URL=os.environ['SUPABASE_URL'].rstrip('/'); SUPABASE_KEY=os.environ['SUPABASE_SERVICE_KEY']
HEADERS={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36','Accept-Language':'tr-TR,tr;q=0.9,en;q=0.8'}
SEEDS={'Amazon':'https://www.amazon.com.tr/gp/goldbox','Hepsiburada':'https://www.hepsiburada.com/ara?q=indirim','Trendyol':'https://www.trendyol.com/sr?q=indirim'}
MIN_DISCOUNT=10.0; COOLDOWN=12; MAX_PRODUCTS_PER_SITE=5; HISTORY_DAYS=90

def sb(method,path,**kwargs):
    h={'apikey':SUPABASE_KEY,'Authorization':f'Bearer {SUPABASE_KEY}','Content-Type':'application/json','Accept':'application/json'}
    if method.upper()=='POST':h['Prefer']='return=representation'
    r=requests.request(method,f'{SUPABASE_URL}/rest/v1/{path}',headers=h,timeout=10,**kwargs)
    if not r.ok:raise RuntimeError(f'Supabase {r.status_code}: {r.text[:300]}')
    return r.json() if r.text else []

def price(v):
    if v is None:return None
    s=re.sub(r'[^0-9,.]','',str(v).replace('TL','').replace('₺','').replace(' ','')).strip()
    if not s:return None
    if ',' in s and '.' in s:s=s.replace('.','').replace(',','.') if s.rfind(',')>s.rfind('.') else s.replace(',','')
    elif ',' in s:
        a,b=s.rsplit(',',1);s=a.replace('.','')+'.'+b if len(b)<=2 else s.replace(',','')
    elif '.' in s:
        a,b=s.rsplit('.',1);s=s.replace('.','') if len(b)>2 else s
    try:return float(s)
    except:return None

def prices(text):
    out=[]
    for m in re.finditer(r'(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)',text or '',re.I):
        p=price(m.group(1))
        if p and 1<p<10000000:out.append(p)
    return out

def canonical(u):
    u=unquote(u or '').replace('\\/','/').strip('"\'<> ')
    p=urlparse(u)
    return f'https://{p.netloc.lower()}{p.path.rstrip("/")}' if p.netloc else u

def valid(site,u):
    p=urlparse(u).path.lower()
    if site=='Amazon':return bool(re.search(r'/(?:dp|gp/product|gp/aw/d)/[a-z0-9]{8,}(?:/|$)',p,re.I))
    return bool(re.search(r'-p-[a-z0-9]+(?:/|$)',p,re.I))

def unwrap(u):
    u=unquote(u or '').replace('\\/','/')
    for _ in range(4):
        q=parse_qs(urlparse(u).query);found=None
        for k in ('url','uddg','u','q','target'):
            if q.get(k) and q[k][0].startswith(('http://','https://')):found=q[k][0];break
        if not found:break
        u=unquote(found)
    return u

def add_url(site,raw,base,out,seen,title='Ürün'):
    if not raw:return
    u=canonical(urljoin(base,unwrap(str(raw))))
    if valid(site,u) and u not in seen:
        seen.add(u);out.append((u,re.sub(r'\s+',' ',title or 'Ürün').strip()[:250]))

def candidates(site,html,base):
    html=html.replace('\\/','/').replace('\\u002F','/').replace('&amp;','&')
    soup=BeautifulSoup(html,'html.parser');out=[];seen=set()
    for a in soup.find_all('a',href=True):
        add_url(site,a.get('href'),base,out,seen,a.get('title') or a.get('aria-label') or a.get_text(' ',strip=True))
        if len(out)>=MAX_PRODUCTS_PER_SITE:return out
    # Also inspect plain text, including RSS <link> elements and JSON embedded URLs.
    raw_text=soup.get_text(' ',strip=True)
    blobs=[html,raw_text]
    if site=='Amazon':
        pats=[r'https?://(?:www\.)?amazon\.com\.tr/(?:dp|gp/product|gp/aw/d)/[A-Za-z0-9]{8,}',r'/(?:dp|gp/product|gp/aw/d)/[A-Za-z0-9]{8,}']
    elif site=='Trendyol':
        pats=[r'https?://(?:www\.)?trendyol\.com/[^"\'<>\\\s]+-p-\d+',r'(?:https?://)?(?:www\.)?trendyol\.com/[^"\'<>\\\s]+-p-\d+']
    else:
        pats=[r'https?://(?:www\.)?hepsiburada\.com/[^"\'<>\\\s]+-p-[A-Za-z0-9]+',r'(?:https?://)?(?:www\.)?hepsiburada\.com/[^"\'<>\\\s]+-p-[A-Za-z0-9]+']
    for blob in blobs:
        for pat in pats:
            for m in re.findall(pat,blob,re.I):
                add_url(site,m,base,out,seen)
                if len(out)>=MAX_PRODUCTS_PER_SITE:return out
    return out

def rss_candidates(site,term):
    domain='hepsiburada.com' if site=='Hepsiburada' else 'trendyol.com'
    q=quote(f'site:{domain} inurl:-p- {term}')
    urls=[f'https://www.bing.com/search?format=rss&q={q}',f'https://html.duckduckgo.com/html/?q={q}']
    out=[];seen=set()
    for u in urls:
        try:
            r=requests.get(u,headers=HEADERS,timeout=10)
            print(f'{site} arama {urlparse(u).netloc} [{term}] HTTP: {r.status_code}')
            if not r.ok:continue
            # Bing RSS puts result URLs inside <link> text, not href attributes.
            soup=BeautifulSoup(r.text,'xml' if 'format=rss' in u else 'html.parser')
            for item in soup.find_all('item'):
                link=item.find('link');title=item.find('title')
                if link and link.get_text(strip=True):add_url(site,link.get_text(strip=True),u,out,seen,title.get_text(' ',strip=True) if title else 'Ürün')
            # Normal HTML engines still need anchor/embedded-URL parsing.
            for x in candidates(site,r.text,u):
                if x[0] not in seen:seen.add(x[0]);out.append(x)
            print(f'{site} arama [{term}] bu kaynaktan aday: {len(out)}')
            if len(out)>=MAX_PRODUCTS_PER_SITE:return out[:MAX_PRODUCTS_PER_SITE]
        except Exception as e:print(f'{site} arama hata: {type(e).__name__}: {e}')
    return out

def search_fallback(site):
    terms=['indirim','çok satan','elektronik','telefon','laptop','kulaklık','televizyon','oyuncu','ev yaşam']
    allout=[];seen=set()
    for term in terms:
        got=rss_candidates(site,term)
        for x in got:
            if x[0] not in seen:seen.add(x[0]);allout.append(x)
        print(f'{site} arama [{term}] toplam aday: {len(allout)}')
        if len(allout)>=MAX_PRODUCTS_PER_SITE:return allout[:MAX_PRODUCTS_PER_SITE]
    return allout

def discover(site,seed,browser):
    page=browser.new_page();page.set_default_timeout(4000);page.set_default_navigation_timeout(15000)
    try:
        r=page.goto(seed,wait_until='domcontentloaded');status=r.status if r else 0;print(f'{site} web HTTP: {status}')
        if not r:return []
        page.wait_for_timeout(1800);got=candidates(site,page.content(),seed);print(f'{site} sayfa aday: {len(got)}');return got
    except Exception as e:print(f'{site} discover hata: {type(e).__name__}: {e}');return []
    finally:page.close()

def jsonld_all(html):
    vals=[];name=None
    for s in BeautifulSoup(html,'html.parser').find_all('script',type='application/ld+json'):
        try:
            x=json.loads(s.string or s.get_text());stack=x if isinstance(x,list) else [x]
            for o in stack:
                if not isinstance(o,dict):continue
                typ=o.get('@type');types=typ if isinstance(typ,list) else [typ]
                if 'Product' in types:
                    name=name or o.get('name');off=o.get('offers') or {}
                    offs=off if isinstance(off,list) else [off]
                    for z in offs:
                        if isinstance(z,dict):
                            for k in ('price','lowPrice','highPrice'):
                                p=price(z.get(k));
                                if p:vals.append(p)
        except:pass
    return name,vals

def product_page(site,url,title,browser):
    page=browser.new_page();page.set_default_timeout(4500);page.set_default_navigation_timeout(15000)
    try:
        r=page.goto(url,wait_until='domcontentloaded');status=r.status if r else 0;print(f'{site} ürün HTTP: {status} | {url}')
        if not r or status>=400:return None
        page.wait_for_timeout(1500);html=page.content();text=page.locator('body').inner_text(timeout=4500)
        jd_name,jd_prices=jsonld_all(html);vals=list(jd_prices)
        selectors=['.a-price .a-offscreen','#corePrice_feature_div .a-offscreen','.apexPriceToPay .a-offscreen','.a-text-price .a-offscreen','.priceBlockStrikePriceString','.basisPrice .a-offscreen','meta[property="product:price:amount"]','meta[itemprop="price"]','[data-test-id*="price"]','[class*="price"]']
        for sel in selectors:
            try:
                loc=page.locator(sel);n=min(loc.count(),15)
                for i in range(n):
                    raw=loc.nth(i).get_attribute('content') if sel.startswith('meta') else loc.nth(i).inner_text(timeout=300)
                    v=price(raw)
                    if v and 1<v<10000000:vals.append(v)
            except:pass
        vals.extend(prices(text));vals=sorted(set(v for v in vals if 1<v<10000000))
        if not vals:
            print(f'{site} fiyat bulunamadı | title={title[:100]} | body={text[:300].replace(chr(10)," ")}')
            return None
        cur=vals[0];prev=next((v for v in vals if v>cur*1.03),None)
        name=jd_name or title or 'Ürün'
        try:name=page.locator('meta[property="og:title"]').get_attribute('content') or name
        except:pass
        print(f'{site} fiyat adayları: {[round(x,2) for x in vals[:12]]}')
        print(f'{site} fiyat: {cur:.2f} | önceki: {prev or 0:.2f}')
        return {'name':re.sub(r'\s+',' ',name).strip()[:300],'price':cur,'previous':prev,'url':canonical(url),'site':site}
    except Exception as e:print(f'{site} ürün hata: {type(e).__name__}: {e}');return None
    finally:page.close()

def history(url):
    try:
        rows=sb('GET','price_history',params={'select':'price,recorded_at','product_url':f'eq.{url}','recorded_at':f'gte.{(datetime.now(timezone.utc)-timedelta(days=HISTORY_DAYS)).isoformat()}','order':'recorded_at.desc','limit':'100'})
        return [float(x['price']) for x in rows if x.get('price') is not None]
    except Exception as e:print(f'history hata: {e}');return []

def process(p):
    try:
        rows=sb('GET','products',params={'select':'*','product_url':f'eq.{p["url"]}','limit':'1'})
        now=datetime.now(timezone.utc).isoformat();payload={'product_name':p['name'],'current_price':p['price'],'previous_price':p.get('previous'),'product_url':p['url'],'site':p['site'],'updated_at':now}
        if rows:row=rows[0];sb('PATCH',f'products?id=eq.{row["id"]}',json=payload)
        else:row=(sb('POST','products',json=payload) or [payload])[0]
        old=history(p['url']);sb('POST','price_history',json={'price':p['price'],'product_url':p['url'],'site':p['site'],'recorded_at':now})
        base=p.get('previous') or (max(old) if old else None)
        print(f'Kontrol: {p["site"]} | mevcut={p["price"]:.2f} | baz={base or 0:.2f} | geçmiş={len(old)}')
        if not base or base<=p['price']:return False
        disc=(base-p['price'])/base*100
        if disc<MIN_DISCOUNT:return False
        last=row.get('last_posted_at') if isinstance(row,dict) else None
        if last:
            try:
                if datetime.now(timezone.utc)-datetime.fromisoformat(last.replace('Z','+00:00'))<timedelta(hours=COOLDOWN):return False
            except:pass
        msg=f'🔥 %{disc:.0f} İNDİRİM\n\n{p["name"]}\n\n💰 {p["price"]:,.2f} TL\n🏷️ Önce: {base:,.2f} TL\n🛍️ {p["site"]}\n🔗 {p["url"]}'
        r=requests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage',json={'chat_id':CHANNEL_ID,'text':msg},timeout=8)
        print(f'Telegram gönderim HTTP: {r.status_code} | {r.text[:200]}')
        if r.ok:
            if isinstance(row,dict) and row.get('id'):sb('PATCH',f'products?id=eq.{row["id"]}',json={'last_posted_at':now,'last_posted_price':p['price']})
            return True
    except Exception as e:print(f'işlem hata: {type(e).__name__}: {e}')
    return False

def main():
    print('=== İndirim botu başladı ===');sent=0
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--disable-blink-features=AutomationControlled'])
        for site,seed in SEEDS.items():
            print(f'--- {site} keşif ---');links=discover(site,seed,browser)
            if not links and site!='Amazon':links=search_fallback(site)
            found=0
            for url,title in links[:MAX_PRODUCTS_PER_SITE]:
                p=product_page(site,url,title,browser)
                if p:found+=1;sent+=1 if process(p) else 0
            print(f'{site}: {found} fiyatlı ürün')
        browser.close()
    print(f'=== Bitti. Gönderilen: {sent} ===')

if __name__=='__main__':main()
