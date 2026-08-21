import os,re,json,statistics,time,html
from datetime import datetime,timezone,timedelta
from urllib.parse import urlparse,urlunparse,urlencode
import requests
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

# API-free watcher: Telegram-discovered products become our own monitored catalog.
# No Serper/paid search API calls are made here.
BASE=(os.environ.get('SUPABASE_URL') or '').rstrip('/')
if BASE.endswith('/rest/v1'):
    BASE=BASE[:-8].rstrip('/')
KEY=os.environ['SUPABASE_SERVICE_KEY']
TOKEN=os.environ['TELEGRAM_BOT_TOKEN']
CHANNEL_ID=os.environ.get('TELEGRAM_CHANNEL_ID','-1004424116637')
AMAZON_TAG=(os.environ.get('AMAZON_ASSOCIATE_TAG') or os.environ.get('AMAZON_TAG') or '').strip()
MIN_DISCOUNT=float(os.environ.get('MIN_DISCOUNT','15'))
MAX_PRODUCTS=max(20,int(os.environ.get('DIRECT_MAX_PRODUCTS','70')))
HISTORY_DAYS=max(30,int(os.environ.get('DIRECT_HISTORY_DAYS','90')))
HISTORY_WRITE_HOURS=max(2,int(os.environ.get('DIRECT_HISTORY_WRITE_HOURS','6')))
BROWSER_LIMIT=max(5,int(os.environ.get('DIRECT_BROWSER_LIMIT','25')))
COOLDOWN_HOURS=max(6,int(os.environ.get('DIRECT_COOLDOWN_HOURS','12')))
HEAD={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36','Accept-Language':'tr-TR,tr;q=0.9,en;q=0.8'}

STATS={'catalog':0,'checked':0,'http_live':0,'browser':0,'no_price':0,'no_ref':0,'below':0,'oos':0,'sent':0,'errors':0,'cooldown':0,'history_writes':0}

def sb(method,path,**kw):
    h={'apikey':KEY,'Authorization':f'Bearer {KEY}','Content-Type':'application/json','Accept':'application/json'}
    if method.upper()=='POST':h['Prefer']='return=representation'
    url=f'{BASE}/rest/v1/{path.lstrip("/")}'
    r=requests.request(method,url,headers=h,timeout=15,**kw)
    if not r.ok:raise RuntimeError(f'Supabase {r.status_code}: {r.text[:250]} | URL={url}')
    return r.json() if r.text else []

def num(v):
    if v is None:return None
    s=re.sub(r'[^0-9,.]','',str(v))
    if not s:return None
    if ',' in s and '.' in s:s=s.replace('.','').replace(',','.') if s.rfind(',')>s.rfind('.') else s.replace(',','')
    elif ',' in s:
        a,b=s.rsplit(',',1);s=a.replace('.','')+'.'+b if len(b)<=2 else s.replace(',','')
    elif '.' in s:
        a,b=s.rsplit('.',1);s=s.replace('.','') if len(b)>2 else s
    try:
        x=float(s);return x if 1<x<10000000 else None
    except:return None

def fmt(x):return f'{x:,.2f}'.replace(',','X').replace('.',',').replace('X','.')

def canonical(u):
    try:
        p=urlparse(u or '')
        return urlunparse(('https',p.netloc.lower(),p.path.rstrip('/'),'','',''))
    except:return u

def site_of(u,stored=''):
    h=urlparse(u or '').netloc.lower()
    if 'amazon.com.tr' in h:return 'Amazon'
    if 'hepsiburada.com' in h:return 'Hepsiburada'
    if 'trendyol.com' in h:return 'Trendyol'
    return stored or ''

def outlink(u,site):
    base=canonical(u)
    if site=='Amazon' and AMAZON_TAG:return base+'?'+urlencode({'tag':AMAZON_TAG})
    return base

def parse_html(body,expected=None):
    soup=BeautifulSoup(body,'html.parser');text=re.sub(r'\s+',' ',soup.get_text(' ',strip=True));low=text.lower()
    if any(x in low for x in ['stokta yok','stokta bulunmuyor','ürün tükendi','urun tukendi','currently unavailable','out of stock','sold out']):
        return {'oos':True}
    title='';image='';live=[];old=[]
    for sel,attr in [('meta[property="og:title"]','content'),('h1',None),('title',None)]:
        e=soup.select_one(sel)
        if e:
            title=re.sub(r'\s+',' ',(e.get(attr) if attr else e.get_text(' ',strip=True)) or '').strip()[:300]
            if title:break
    for sel,attr in [('meta[property="og:image"]','content'),('meta[name="twitter:image"]','content'),('img[itemprop="image"]','src')]:
        e=soup.select_one(sel)
        if e and (e.get(attr) or '').startswith('http'):image=e.get(attr);break
    for sel,attr in [('meta[property="product:price:amount"]','content'),('meta[itemprop="price"]','content'),('[itemprop="price"]','content'),('.a-price .a-offscreen',None),('.apexPriceToPay .a-offscreen',None),('[data-test-id="price-current-price"]',None),('[class*="currentPrice"]',None),('[class*="salePrice"]',None),('.prc-dsc',None),('.prc-slg',None)]:
        for e in soup.select(sel)[:15]:
            p=num(e.get(attr) if attr else e.get_text(' ',strip=True))
            if p and (not expected or expected*.45<=p<=expected*1.8):live.append(p)
    for sel in ['del','s','.old-price','.list-price','[class*="oldPrice"]','[class*="listPrice"]','.a-text-price .a-offscreen','.basisPrice .a-offscreen']:
        for e in soup.select(sel)[:15]:
            p=num(e.get('content') or e.get('data-price') or e.get_text(' ',strip=True))
            if p:old.append(p)
    live=sorted(set(round(x,2) for x in live));old=sorted(set(round(x,2) for x in old))
    cur=None
    if live:
        cur=min(live,key=lambda p:abs(p-expected)) if expected else min(live)
    oldv=None
    if cur:
        cand=[x for x in old if cur*1.03<x<=cur*1.8]
        if cand:oldv=float(statistics.median(cand))
    return {'oos':False,'live':cur,'old':oldv,'title':title,'image':image}

def http_check(url,expected):
    try:
        r=requests.get(url,headers=HEAD,timeout=10,allow_redirects=True)
        if not r.ok:return None
        return parse_html(r.text,expected)
    except:return None

def browser_check(page,url,expected):
    try:
        page.goto(url,wait_until='domcontentloaded',timeout=14000);page.wait_for_timeout(1000)
        return parse_html(page.content(),expected)
    except:return None

def history(url):
    since=(datetime.now(timezone.utc)-timedelta(days=HISTORY_DAYS)).isoformat()
    try:
        rows=sb('GET','price_history',params={'select':'price,recorded_at','product_url':f'eq.{canonical(url)}','recorded_at':f'gte.{since}','order':'recorded_at.desc','limit':'120'})
        return [(float(x['price']),x.get('recorded_at')) for x in rows if x.get('price') not in (None,0)]
    except Exception as e:
        print(f'GEÇMİŞ HATA: {type(e).__name__}: {e}');return []

def reference(current,hist,page_old,stored_prev=None):
    higher=[p for p,_ in hist if current*1.03<p<=current*1.65]
    href=float(statistics.median(higher)) if len(higher)>=2 else (higher[0] if higher else None)
    local=[x for x in [href,page_old,stored_prev] if x and current*1.03<x<=current*1.65]
    if not local:return None,'none'
    # Conservative: use the lower credible local reference to avoid fake discounts.
    return float(min(local)),'history/page'

def maybe_write_history(url,site,current,hist):
    last=hist[0] if hist else None
    write=False
    if not last:write=True
    else:
        lp,dt=last
        if abs(lp-current)/max(current,1)>=0.005:write=True
        else:
            try:write=datetime.now(timezone.utc)-datetime.fromisoformat((dt or '').replace('Z','+00:00'))>=timedelta(hours=HISTORY_WRITE_HOURS)
            except:write=True
    if write:
        sb('POST','price_history',json={'price':current,'product_url':canonical(url),'site':site,'recorded_at':datetime.now(timezone.utc).isoformat()});STATS['history_writes']+=1

def send(row,current,ref,title,image,site):
    last=row.get('last_posted_at')
    if last:
        try:
            if datetime.now(timezone.utc)-datetime.fromisoformat(last.replace('Z','+00:00'))<timedelta(hours=COOLDOWN_HOURS):STATS['cooldown']+=1;return False
        except:pass
    disc=(ref-current)/ref*100
    url=outlink(row['product_url'],site)
    lines=['⭐️⭐️⭐️ 🔥 %%.0f İNDİRİM'%disc,'',f'🛍️ {html.escape(title)}',f'💰 {fmt(current)} TL',f'🏷️ Referans fiyat: {fmt(ref)} TL',f'🛍️ {site}','','👇 <a href="'+html.escape(url,quote=True)+'"><b>Fırsata git</b></a>']
    text='\n'.join(lines);kb={'inline_keyboard':[[{'text':'🛒 FIRSATA GİT','url':url}]]};resp=None
    if image:
        try:resp=requests.post(f'https://api.telegram.org/bot{TOKEN}/sendPhoto',data={'chat_id':CHANNEL_ID,'photo':image,'caption':text[:1024],'parse_mode':'HTML','reply_markup':json.dumps(kb,ensure_ascii=False)},timeout=18)
        except:resp=None
    if not resp or not resp.ok:
        resp=requests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage',json={'chat_id':CHANNEL_ID,'text':text,'parse_mode':'HTML','disable_web_page_preview':False,'reply_markup':kb},timeout=18)
    if not resp.ok:raise RuntimeError(f'Telegram {resp.status_code}: {resp.text[:180]}')
    sb('PATCH',f'products?id=eq.{row["id"]}',json={'last_posted_at':datetime.now(timezone.utc).isoformat(),'last_posted_price':current})
    STATS['sent']+=1;return True

def load_catalog():
    # Avoid special PostgREST filters that previously produced invalid paths.
    rows=sb('GET','products',params={'select':'id,product_name,current_price,previous_price,product_url,site,updated_at,last_posted_at,last_posted_price','order':'updated_at.asc.nullsfirst','limit':str(MAX_PRODUCTS*3)})
    good=[]
    for r in rows:
        u=r.get('product_url') or '';s=site_of(u,r.get('site') or '')
        if s not in {'Amazon','Hepsiburada','Trendyol'}:continue
        if not u.startswith('http'):continue
        r['site']=s;good.append(r)
        if len(good)>=MAX_PRODUCTS:break
    return good

def main():
    print(f'=== API-SİZ fiyat takip V2 | limit={MAX_PRODUCTS} | geçmiş={HISTORY_DAYS}g | browser_limit={BROWSER_LIMIT} ===')
    try:rows=load_catalog()
    except Exception as e:
        print(f'ÜRÜN LİSTESİ HATA: {type(e).__name__}: {e}');raise
    STATS['catalog']=len(rows);print(f'KATALOG: {len(rows)} ürün')
    browser_used=0
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--disable-blink-features=AutomationControlled']);page=browser.new_page()
        for row in rows:
            try:
                STATS['checked']+=1;url=canonical(row['product_url']);site=row['site'];expected=num(row.get('current_price'))
                info=http_check(url,expected)
                if info and info.get('oos'):
                    STATS['oos']+=1;print(f'STOKTA YOK: {site} | {row.get("product_name","")[:80]}');continue
                if info and info.get('live'):STATS['http_live']+=1
                if (not info or not info.get('live')) and browser_used<BROWSER_LIMIT:
                    browser_used+=1;STATS['browser']+=1;info=browser_check(page,url,expected) or info
                if not info or not info.get('live'):
                    STATS['no_price']+=1;print(f'FİYAT YOK: {site} | {row.get("product_name","")[:90]}');continue
                current=float(info['live']);hist=history(url);ref,rsrc=reference(current,hist,info.get('old'),num(row.get('previous_price')))
                maybe_write_history(url,site,current,hist)
                title=(info.get('title') or row.get('product_name') or 'Ürün')[:300]
                sb('PATCH',f'products?id=eq.{row["id"]}',json={'product_name':title,'current_price':current,'product_url':url,'site':site,'updated_at':datetime.now(timezone.utc).isoformat()})
                if not ref:
                    STATS['no_ref']+=1;print(f'REFERANS YOK: {site} | {current:.2f} | geçmiş={len(hist)} | {title[:75]}');continue
                disc=(ref-current)/ref*100
                print(f'KONTROL: {site} | {current:.2f} -> ref {ref:.2f} | %{disc:.1f} | geçmiş={len(hist)} | {title[:70]}')
                if disc<MIN_DISCOUNT:STATS['below']+=1;continue
                send(row,current,ref,title,info.get('image'),site)
            except Exception as e:
                STATS['errors']+=1;print(f'ÜRÜN HATA: {type(e).__name__}: {e}')
        browser.close()
    print('=== API-SİZ V2 BİTTİ | '+' | '.join(f'{k}={v}' for k,v in STATS.items())+' ===')

if __name__=='__main__':main()
