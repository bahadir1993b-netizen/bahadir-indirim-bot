import os,re,json
from datetime import datetime,timezone,timedelta
from urllib.parse import urljoin,urlparse,quote,parse_qs,unquote
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

TOKEN=os.environ['TELEGRAM_BOT_TOKEN']; CHANNEL_ID='-1004424116637'
SUPABASE_URL=os.environ['SUPABASE_URL'].rstrip('/'); SUPABASE_KEY=os.environ['SUPABASE_SERVICE_KEY']
HEADERS={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36','Accept-Language':'tr-TR,tr;q=0.9,en;q=0.8'}
SEEDS={'Amazon':'https://www.amazon.com.tr/gp/goldbox','Hepsiburada':'https://www.hepsiburada.com/ara?q=indirim','Trendyol':'https://www.trendyol.com/sr?q=indirim'}
MIN_DISCOUNT=10.0; COOLDOWN=12; MAX_PRODUCTS_PER_SITE=2; HISTORY_DAYS=90; MIN_HISTORY=3


def sb(method,path,**kwargs):
    h={'apikey':SUPABASE_KEY,'Authorization':f'Bearer {SUPABASE_KEY}','Content-Type':'application/json','Accept':'application/json'}
    if method.upper()=='POST': h['Prefer']='return=representation'
    r=requests.request(method,f'{SUPABASE_URL}/rest/v1/{path}',headers=h,timeout=10,**kwargs)
    if not r.ok: raise RuntimeError(f'Supabase {r.status_code}: {r.text[:300]}')
    return r.json() if r.text else []

def price(v):
    if v is None:return None
    s=re.sub(r'[^0-9,.]','',str(v).replace('TL','').replace('₺','').replace(' ',''))
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
        if p and p>0:out.append(p)
    return out

def canonical(u):
    u=unquote(u or '').replace('\\/','/').strip('"\'')
    p=urlparse(u);return f'{p.scheme or "https"}://{p.netloc.lower()}{p.path.rstrip("/")}'

def valid(site,u):
    p=urlparse(u).path.lower()
    if site=='Amazon':return bool(re.search(r'/(?:dp|gp/product|gp/aw/d)/[a-z0-9]{8,}(?:/|$)',p,re.I))
    if site=='Trendyol':return bool(re.search(r'-p-\d+(?:/|$)',p,re.I))
    return bool(re.search(r'-p-[a-z0-9]+(?:/|$)',p,re.I))

def unwrap(u):
    u=unquote(u or '').replace('\\/','/')
    q=parse_qs(urlparse(u).query)
    for k in ('url','uddg','u','q'):
        if q.get(k) and q[k][0].startswith('http'):return q[k][0]
    return u

def candidates(site,html,base):
    html=html.replace('\\/','/').replace('\\u002F','/').replace('&amp;','&')
    soup=BeautifulSoup(html,'html.parser');out=[];seen=set()
    def add(raw,title='Ürün'):
        if not raw:return
        u=canonical(urljoin(base,unwrap(raw)))
        if valid(site,u) and u not in seen:
            seen.add(u);out.append((u,re.sub(r'\s+',' ',title or 'Ürün').strip()[:250]))
    for a in soup.find_all('a',href=True):
        add(a.get('href'),a.get('title') or a.get('aria-label') or a.get_text(' ',strip=True))
        if len(out)>=MAX_PRODUCTS_PER_SITE:return out
    if site=='Amazon':pat=r'(?:https?:)?//[^\"\'<>\s]+amazon\.com\.tr[^\"\'<>\s]*/(?:dp|gp/product|gp/aw/d)/[A-Za-z0-9]{8,}'
    elif site=='Trendyol':pat=r'(?:https?:)?//[^\"\'<>\s]+trendyol\.com[^\"\'<>\s]+-p-\d+'
    else:pat=r'(?:https?:)?//[^\"\'<>\s]+hepsiburada\.com[^\"\'<>\s]+-p-[A-Za-z0-9]+'
    for m in re.finditer(pat,html,re.I):
        add(m.group(0))
        if len(out)>=MAX_PRODUCTS_PER_SITE:break
    print(f'{site} adaylar: {[u for u,_ in out]}');return out

def sitemap_fallback(site):
    domain='https://www.hepsiburada.com' if site=='Hepsiburada' else 'https://www.trendyol.com'
    try:
        r=requests.get(domain+'/sitemap.xml',headers=HEADERS,timeout=6)
        print(f'{site} sitemap HTTP: {r.status_code}')
        if not r.ok:return []
        xml=r.text[:2000000]
        locs=re.findall(r'<loc>\s*(.*?)\s*</loc>',xml,re.I|re.S)
        product_sitemaps=[u for u in locs if 'sitemap' in u.lower()]
        pages=product_sitemaps[:4] if product_sitemaps else [domain+'/sitemap.xml']
        out=[]
        for sm in pages:
            try:
                rr=requests.get(unescape_xml(sm),headers=HEADERS,timeout=6)
                if not rr.ok:continue
                text=unescape_xml(rr.text[:3000000])
                urls=re.findall(r'<loc>\s*(https?://[^<\s]+)\s*</loc>',text,re.I)
                for u in urls:
                    u=canonical(u)
                    if valid(site,u) and all(u!=x[0] for x in out):
                        out.append((u,'Ürün'))
                        if len(out)>=MAX_PRODUCTS_PER_SITE:return out
            except Exception:pass
        print(f'{site} sitemap adaylar: {[u for u,_ in out]}')
        return out
    except Exception as e:
        print(f'{site} sitemap hata: {type(e).__name__}: {e}');return []

def unescape_xml(s):
    return s.replace('&amp;','&').replace('&quot;','"').replace('&#x2F;','/').replace('&#47;','/')

def discover(site,seed,browser):
    page=browser.new_page();page.set_default_timeout(2500);page.set_default_navigation_timeout(12000)
    try:
        r=page.goto(seed,wait_until='domcontentloaded');print(f'{site} web HTTP: {r.status if r else 0}')
        if not r or r.status>=400:return []
        page.wait_for_timeout(500);return candidates(site,page.content(),seed)
    except Exception as e:print(f'{site} discover hata: {type(e).__name__}: {e}');return []
    finally:page.close()

def search_fallback(site):
    domain='hepsiburada.com' if site=='Hepsiburada' else 'trendyol.com'
    terms=['elektronik','telefon','laptop','kulaklık','televizyon','oyuncu','ev yaşam','indirim']
    for term in terms:
        q=quote(f'site:{domain} {term}')
        for engine in ('bing','google'):
            try:
                u=f'https://www.{engine}.com/search?q={q}';r=requests.get(u,headers=HEADERS,timeout=4)
                print(f'{site} {engine} [{term}] HTTP: {r.status_code}')
                if r.ok:
                    got=candidates(site,r.text,u)
                    if got:return got
            except Exception as e:print(f'{site} {engine} hata: {type(e).__name__}: {e}')
    return sitemap_fallback(site)

def jsonld(html):
    for s in BeautifulSoup(html,'html.parser').find_all('script',type='application/ld+json'):
        try:
            x=json.loads(s.string or s.get_text());stack=x if isinstance(x,list) else [x]
            for o in stack:
                if isinstance(o,dict) and (o.get('@type')=='Product' or 'Product' in (o.get('@type') or [])):
                    off=o.get('offers') or {};off=off[0] if isinstance(off,list) and off else off
                    return {'name':o.get('name'),'price':price(off.get('price')) if isinstance(off,dict) else None}
        except:pass
    return {}

def product_page(site,url,title,browser):
    page=browser.new_page();page.set_default_timeout(1800);page.set_default_navigation_timeout(9000)
    try:
        r=page.goto(url,wait_until='domcontentloaded');print(f'{site} ürün HTTP: {r.status if r else 0} | {url[:110]}')
        if not r or r.status>=400:return None
        page.wait_for_timeout(400);html=page.content();text=page.locator('body').inner_text(timeout=1800);jd=jsonld(html)
        cur=jd.get('price');prev=None
        if site=='Amazon':
            vals=[]
            for sel in ('.a-price .a-offscreen','#corePrice_feature_div .a-offscreen','.apexPriceToPay .a-offscreen','meta[property="product:price:amount"]'):
                try:
                    loc=page.locator(sel);n=min(loc.count(),3)
                    for i in range(n):
                        v=price(loc.nth(i).get_attribute('content') if sel.startswith('meta') else loc.nth(i).inner_text(timeout=500))
                        if v:vals.append(v)
                except:pass
            if vals:
                cur=cur or min(vals);b=[v for v in vals if cur and v>cur*1.03];prev=min(b) if b else None
            for sel in ('.a-text-price .a-offscreen','.priceBlockStrikePriceString','.basisPrice .a-offscreen'):
                if prev is not None:break
                try:
                    v=price(page.locator(sel).first.inner_text(timeout=500))
                    if v and cur and v>cur:prev=v
                except:pass
        if cur is None:cur=min(prices(text),default=None)
        if not cur:return None
        name=jd.get('name') or title
        try:name=page.locator('meta[property="og:title"]').get_attribute('content') or name
        except:pass
        return {'name':re.sub(r'\s+',' ',name or 'Ürün').strip()[:300],'price':cur,'previous':prev,'url':canonical(url),'site':site}
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
        if rows:sb('PATCH',f'products?id=eq.{rows[0]["id"]}',json=payload);row=rows[0]
        else:row=(sb('POST','products',json=payload) or [payload])[0]
        sb('POST','price_history',json={'price':p['price'],'product_url':p['url'],'site':p['site'],'recorded_at':now})
        h=history(p['url']);base=p.get('previous')
        if not base and len(h)>=MIN_HISTORY:base=max(h)
        if not base or base<=p['price']:return False
        if h and min(h)<p['price']*0.95:return False
        disc=(base-p['price'])/base*100
        if disc<MIN_DISCOUNT:return False
        last=row.get('last_posted_at')
        if last:
            try:
                if datetime.now(timezone.utc)-datetime.fromisoformat(last.replace('Z','+00:00'))<timedelta(hours=COOLDOWN):return False
            except:pass
        msg=f"🔥 %{disc:.0f} İNDİRİM\n\n{p['name']}\n\n💰 {p['price']:,.2f} TL\n🏷️ Önce: {base:,.2f} TL\n🛍️ {p['site']} 🔗 {p['url']}"
        r=requests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage',json={'chat_id':CHANNEL_ID,'text':msg},timeout=8)
        print(f'Telegram gönderim HTTP: {r.status_code}')
        if r.ok:
            try:sb('PATCH',f'products?id=eq.{row.get("id")}',json={'last_posted_at':now,'last_posted_price':p['price']})
            except:pass
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
                if p:
                    found+=1;sent+=1 if process(p) else 0
            print(f'{site}: {found} fiyatlı ürün')
        browser.close()
    print(f'=== Bitti. Gönderilen: {sent} ===')

if __name__=='__main__':main()
