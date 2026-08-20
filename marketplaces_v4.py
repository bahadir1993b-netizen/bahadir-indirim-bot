import os,re,requests,json,html as htmlmod,xml.etree.ElementTree as ET
from datetime import datetime,timezone,timedelta
from urllib.parse import quote,urlparse,unquote,urljoin
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
TOKEN=os.environ['TELEGRAM_BOT_TOKEN'];SB=os.environ['SUPABASE_URL'].rstrip('/');KEY=os.environ['SUPABASE_SERVICE_KEY']
APPS=os.environ.get('APPS_SCRIPT_URL','').strip()
CHAT='-1004424116637';MIN_DISCOUNT=10.0;COOLDOWN=12;HISTORY_DAYS=90;MAX_PRODUCTS=12
HEAD={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36','Accept-Language':'tr-TR,tr;q=0.9,en;q=0.8'}
SITES={'Hepsiburada':'hepsiburada.com','Trendyol':'trendyol.com'}
SEARCHES={'Hepsiburada':'https://www.hepsiburada.com/ara?q={}','Trendyol':'https://www.trendyol.com/sr?q={}'}
TERMS=['indirim','fırsat','kampanya','telefon','laptop','televizyon','kulaklık','elektronik','oyuncu','ev yaşam']
def sb(method,path,**kw):
 h={'apikey':KEY,'Authorization':f'Bearer {KEY}','Content-Type':'application/json','Accept':'application/json'}
 if method=='POST':h['Prefer']='return=representation'
 r=requests.request(method,f'{SB}/rest/v1/{path}',headers=h,timeout=15,**kw);r.raise_for_status();return r.json() if r.text else []
def price(v):
 s=re.sub(r'[^0-9,.]','',str(v or '').replace('TL','').replace('₺','').replace(' ',''))
 if not s:return None
 if ',' in s and '.' in s:s=s.replace('.','').replace(',','.') if s.rfind(',')>s.rfind('.') else s.replace(',','')
 elif ',' in s:
  a,b=s.rsplit(',',1);s=a.replace('.','')+'.'+b if len(b)<=2 else s.replace(',','')
 elif '.' in s:
  a,b=s.rsplit('.',1);s=s.replace('.','') if len(b)>2 else s
 try:
  x=float(s);return x if 0<x<10000000 else None
 except:return None
def prices(t):
 out=[]
 for m in re.finditer(r'(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)',t or '',re.I):
  x=price(m.group(1));
  if x is not None:out.append(x)
 return out
def valid(site,u):
 u=unquote(u or '').replace('\\/','/');p=urlparse(u)
 if SITES[site] not in p.netloc.lower():return False
 return bool(re.search(r'-p-'+(r'\d+' if site=='Trendyol' else r'[A-Za-z0-9]+')+r'(?:[/?#&]|$)',p.path,re.I))
def clean(site,u):
 u=htmlmod.unescape(unquote(u or '')).replace('\\/','/').strip('"\'<> ')
 if u.startswith('/'):u=urljoin('https://www.'+SITES[site],u)
 m=re.search(r'https?://(?:www\.)?'+re.escape(SITES[site])+r'/[^\s"<>]+',u,re.I)
 if m:u=m.group(0)
 p=urlparse(u)
 if not valid(site,u):return None
 return 'https://www.'+SITES[site]+p.path.rstrip('/')
def apps_script_search(site,term):
 if not APPS:return []
 try:
  r=requests.get(APPS,params={'site':site,'q':term},headers=HEAD,timeout=25)
  print(f'{site} Apps Script [{term}] HTTP: {r.status_code}')
  data=r.json()
  if not data.get('ok'):print(f'{site} Apps Script hata: {data.get("error")}');return []
  out=[];seen=set()
  for x in data.get('products',[]):
   u=clean(site,x.get('url',''))
   if u and u not in seen:
    seen.add(u);out.append((u,x.get('title') or 'Ürün',[]));print(f'{site} Apps Script adayı: {x.get("title","Ürün")[:80]} | {u}')
  return out[:MAX_PRODUCTS]
 except Exception as e:print(f'{site} Apps Script hata: {type(e).__name__}: {e}');return []
def search_fallback(site,term):
 q=quote(f'site:{SITES[site]} {term}');out=[];seen=set()
 for endpoint,kind in [(f'https://www.bing.com/search?format=rss&q={q}&count=30','BingRSS'),(f'https://html.duckduckgo.com/html/?q={q}','DDG')]:
  try:
   r=requests.get(endpoint,headers=HEAD,timeout=15);print(f'{site} {kind} [{term}] HTTP: {r.status_code}')
   if r.status_code>=400:continue
   if kind=='BingRSS':
    root=ET.fromstring(r.content)
    for item in root.findall('.//item'):
     raw=' '.join([item.findtext('link') or '',item.findtext('description') or '']);title=htmlmod.unescape(item.findtext('title') or 'Ürün')
     for rawu in re.findall(r'https?://(?:www\.)?'+re.escape(SITES[site])+r'/[^\s"<>]+',htmlmod.unescape(raw),re.I):
      u=clean(site,rawu)
      if u and u not in seen:seen.add(u);out.append((u,title[:300],prices(raw)))
   else:
    soup=BeautifulSoup(r.text,'html.parser')
    for a in soup.select('a.result__a,a[href]'):
     raw=htmlmod.unescape(unquote(a.get('href','')));text=a.get_text(' ',strip=True) or 'Ürün'
     for rawu in re.findall(r'https?://(?:www\.)?'+re.escape(SITES[site])+r'/[^\s"<>]+',raw,re.I):
      u=clean(site,rawu)
      if u and u not in seen:seen.add(u);out.append((u,text[:300],prices(text)))
   if len(out)>=MAX_PRODUCTS:return out[:MAX_PRODUCTS]
  except Exception as e:print(f'{site} {kind} hata: {type(e).__name__}: {e}')
 return out[:MAX_PRODUCTS]
def direct_search(site,term,page):
 apps=apps_script_search(site,term)
 if apps:return apps
 url=SEARCHES[site].format(quote(term));out=[];seen=set()
 try:
  r=page.goto(url,wait_until='domcontentloaded',timeout=15000);status=r.status if r else 0;print(f'{site} direkt arama [{term}] HTTP: {status}')
  if status<400:
   page.wait_for_timeout(1800);anchors=page.locator('a[href]');n=min(anchors.count(),500)
   for i in range(n):
    a=anchors.nth(i)
    try:
     u=clean(site,a.get_attribute('href') or '')
     if not u or u in seen:continue
     text=a.inner_text(timeout=200).strip() or a.get_attribute('title') or 'Ürün';block=text
     try:block=a.locator('xpath=..').inner_text(timeout=200).strip() or text
     except:pass
     seen.add(u);out.append((u,re.sub(r'\s+',' ',text)[:300],prices(block)))
     if len(out)>=MAX_PRODUCTS:return out
    except:pass
  else:print(f'{site} direkt arama engellendi; arama motoru fallback devreye giriyor')
 except Exception as e:print(f'{site} direkt arama hata: {type(e).__name__}: {e}')
 return search_fallback(site,term)
def jsonld(html):
 vals=[];name=None
 for s in BeautifulSoup(html,'html.parser').find_all('script',type='application/ld+json'):
  try:
   o=json.loads(s.string or s.get_text());stack=o if isinstance(o,list) else [o]
   for x in stack:
    if not isinstance(x,dict):continue
    typ=x.get('@type');typ=typ if isinstance(typ,list) else [typ]
    if 'Product' not in typ:continue
    name=name or x.get('name');off=x.get('offers') or [];off=off if isinstance(off,list) else [off]
    for z in off:
     if isinstance(z,dict):
      for k in ('price','lowPrice','highPrice'):
       p=price(z.get(k));
       if p is not None:vals.append(p)
  except:pass
 return name,sorted(set(vals))
def product_page(site,url,title,snip,browser):
 p=browser.new_page();p.set_default_timeout(5000);p.set_default_navigation_timeout(15000)
 try:
  r=p.goto(url,wait_until='domcontentloaded');status=r.status if r else 0;print(f'{site} ürün HTTP: {status} | {url}')
  if not r or status>=400:return None
  p.wait_for_timeout(900);html=p.content();name,jd=jsonld(html);cur=None
  sels=['meta[property="product:price:amount"]','meta[itemprop="price"]']
  sels+=['[data-testid="price-current"]','[class*="prc-dsc"]','[class*="price-current"]'] if site=='Trendyol' else ['[data-test-id="price-current"]','[data-test-id="current-price"]','[class*="product-price"]','[class*="current-price"]']
  for sel in sels:
   try:
    loc=p.locator(sel)
    for i in range(min(loc.count(),10)):
     raw=loc.nth(i).get_attribute('content') if sel.startswith('meta') else loc.nth(i).inner_text(timeout=300);x=price(raw)
     if x is not None:cur=x;break
   except:pass
   if cur is not None:break
  if cur is None and jd:cur=jd[0]
  if cur is None:
   ps=prices(p.locator('body').inner_text(timeout=4000));cur=ps[0] if ps else None
  if cur is None:return None
  try:name=p.locator('meta[property="og:title"]').get_attribute('content') or name
  except:pass
  return {'url':url,'title':re.sub(r'\s+',' ',name or title or 'Ürün').strip()[:300],'current':cur}
 except Exception as e:print(f'{site} ürün hata: {type(e).__name__}: {e}');return None
 finally:p.close()
def history(url):
 since=(datetime.now(timezone.utc)-timedelta(days=HISTORY_DAYS)).isoformat()
 try:
  rows=sb('GET','price_history',params={'select':'price,recorded_at','product_url':f'eq.{url}','recorded_at':f'gte.{since}','order':'recorded_at.desc','limit':'100'});return [float(x['price']) for x in rows]
 except:return []
def process(p,site):
 now=datetime.now(timezone.utc).isoformat();old=history(p['url']);prev=old[0] if old else None
 rows=sb('GET','products',params={'select':'*','product_url':f'eq.{p["url"]}','limit':'1'});row=rows[0] if rows else None
 payload={'product_name':p['title'],'current_price':p['current'],'previous_price':prev,'product_url':p['url'],'site':site,'updated_at':now}
 if row:sb('PATCH',f'products?id=eq.{row["id"]}',json=payload)
 else:row=(sb('POST','products',json=payload) or [payload])[0]
 sb('POST','price_history',json={'price':p['current'],'product_url':p['url'],'site':site,'recorded_at':now})
 print(f'Kontrol: {site} | mevcut={p["current"]:.2f} | son gözlenen={prev or 0:.2f} | geçmiş={len(old)}')
 if prev is None or prev<=p['current']:return False
 disc=(prev-p['current'])/prev*100
 if disc<MIN_DISCOUNT:return False
 last=row.get('last_posted_at') if isinstance(row,dict) else None
 if last:
  try:
   if datetime.now(timezone.utc)-datetime.fromisoformat(last.replace('Z','+00:00'))<timedelta(hours=COOLDOWN):return False
  except:pass
 msg=f'🔥 %{disc:.0f} İNDİRİM\n\n{p["title"]}\n\n💰 {p["current"]:,.2f} TL\n🏷️ Önce: {prev:,.2f} TL\n🛍️ {site}\n🔗 {p["url"]}'
 requests.post('https://api.telegram.org/bot'+TOKEN+'/sendMessage',json={'chat_id':CHAT,'text':msg},timeout=15).raise_for_status();sb('PATCH',f'products?id=eq.{row["id"]}',json={'last_posted_at':now});return True
def main():
 print('=== HB/Trendyol V4 keşif başladı ===');sent=0
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True,args=['--no-sandbox']);search=browser.new_page()
  for site in ('Hepsiburada','Trendyol'):
   found=[];seen=set()
   for term in TERMS:
    for item in direct_search(site,term,search):
     if item[0] not in seen:seen.add(item[0]);found.append(item)
     if len(found)>=MAX_PRODUCTS:break
    if len(found)>=MAX_PRODUCTS:break
   print(f'{site}: {len(found)} aday')
   for url,title,snip in found:
    p=product_page(site,url,title,snip,browser)
    if p:
     try:sent+=1 if process(p,site) else 0
     except Exception as e:print(f'{site} işlem hata: {type(e).__name__}: {e}')
  search.close();browser.close()
 print(f'=== V4 bitti. Gönderilen: {sent} ===')
if __name__=='__main__':main()
