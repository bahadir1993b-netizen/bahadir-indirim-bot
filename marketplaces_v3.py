import os,re,requests,html as htmlmod,xml.etree.ElementTree as ET,json
from datetime import datetime,timezone,timedelta
from urllib.parse import quote,urlparse,unquote
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

TOKEN=os.environ['TELEGRAM_BOT_TOKEN']; SUPABASE_URL=os.environ['SUPABASE_URL'].rstrip('/'); SUPABASE_KEY=os.environ['SUPABASE_SERVICE_KEY']
CHANNEL_ID='-1004424116637'; MIN_DISCOUNT=10.0; COOLDOWN=12; HISTORY_DAYS=90; MAX_PRODUCTS=10
HEADERS={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36','Accept-Language':'tr-TR,tr;q=0.9,en;q=0.8'}
SITES={'Hepsiburada':'hepsiburada.com','Trendyol':'trendyol.com'}
TERMS=['indirim','fırsat','kampanya','telefon','laptop','kulaklık','televizyon','elektronik','oyuncu','ev yaşam']

def sb(method,path,**kwargs):
 h={'apikey':SUPABASE_KEY,'Authorization':f'Bearer {SUPABASE_KEY}','Content-Type':'application/json','Accept':'application/json'}
 if method=='POST': h['Prefer']='return=representation'
 r=requests.request(method,f'{SUPABASE_URL}/rest/v1/{path}',headers=h,timeout=15,**kwargs); r.raise_for_status(); return r.json() if r.text else []

def price(v):
 s=re.sub(r'[^0-9,.]','',str(v or '').replace('TL','').replace('₺','').replace(' ',''))
 if not s:return None
 if ',' in s and '.' in s:s=s.replace('.','').replace(',','.') if s.rfind(',')>s.rfind('.') else s.replace(',','')
 elif ',' in s:
  a,b=s.rsplit(',',1); s=a.replace('.','')+'.'+b if len(b)<=2 else s.replace(',','')
 elif '.' in s:
  a,b=s.rsplit('.',1); s=s.replace('.','') if len(b)>2 else s
 try:
  x=float(s); return x if 0<x<10000000 else None
 except:return None

def prices(t):
 out=[]
 for m in re.finditer(r'(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)',t or '',re.I):
  x=price(m.group(1));
  if x is not None:out.append(x)
 return out

def valid(site,u):
 u=unquote(u or '').replace('\\/','/'); p=urlparse(u)
 if SITES[site] not in p.netloc.lower():return False
 if site=='Trendyol':return bool(re.search(r'-p-\d+(?:[/?#&]|$)',p.path,re.I))
 return bool(re.search(r'-p-[A-Za-z0-9]+(?:[/?#&]|$)',p.path,re.I))

def clean(site,u):
 u=htmlmod.unescape(unquote(u or '')).replace('\\/','/').strip('"\'<> ')
 m=re.search(r'https?://(?:www\.)?'+re.escape(SITES[site])+r'/[^\s"<>]+',u,re.I)
 if m:u=m.group(0)
 p=urlparse(u)
 if not valid(site,u):return None
 return 'https://www.'+SITES[site]+p.path.rstrip('/')

def rss_search(site,term):
 q=quote(f'site:{SITES[site]} {term}')
 url=f'https://www.bing.com/search?format=rss&q={q}&count=30'
 out=[];seen=set()
 try:
  r=requests.get(url,headers=HEADERS,timeout=15); print(f'{site} Bing RSS [{term}] HTTP: {r.status_code}')
  if r.status_code>=400:return []
  root=ET.fromstring(r.content)
  for item in root.findall('.//item'):
   link=item.findtext('link') or ''; title=htmlmod.unescape(item.findtext('title') or 'Ürün'); desc=htmlmod.unescape(item.findtext('description') or '')
   u=clean(site,link)
   if not u:
    for m in re.findall(r'https?://(?:www\.)?'+re.escape(SITES[site])+r'/[^\s"<>]+',desc,re.I):
     u=clean(site,m)
     if u:break
   if u and u not in seen:
    seen.add(u); ps=prices(desc+' '+title); out.append((u,re.sub(r'\s+',' ',title).strip()[:300],ps))
    print(f'{site} RSS aday: {title[:90]} | fiyatlar={ps[:6]} | {u}')
    if len(out)>=MAX_PRODUCTS:return out
 except Exception as e:print(f'{site} RSS hata: {type(e).__name__}: {e}')
 return out

def html_search(site,term):
 q=quote(f'site:{SITES[site]} {term}')
 url=f'https://www.bing.com/search?q={q}&count=30'
 out=[];seen=set()
 try:
  r=requests.get(url,headers=HEADERS,timeout=15); print(f'{site} Bing HTML [{term}] HTTP: {r.status_code}')
  if r.status_code>=400:return []
  soup=BeautifulSoup(r.text,'html.parser')
  for a in soup.find_all('a',href=True):
   raw=htmlmod.unescape(unquote(a.get('href','')))
   for m in re.findall(r'https?://(?:www\.)?'+re.escape(SITES[site])+r'/[^\s"<>]+-p-[A-Za-z0-9]+',raw,re.I):
    u=clean(site,m)
    if u and u not in seen:
     seen.add(u); block=a.parent.get_text(' ',strip=True) if a.parent else a.get_text(' ',strip=True); out.append((u,a.get_text(' ',strip=True)[:300] or 'Ürün',prices(block)))
     if len(out)>=MAX_PRODUCTS:return out
 except Exception as e:print(f'{site} HTML hata: {type(e).__name__}: {e}')
 return out

def discover(site):
 found=[];seen=set()
 for term in TERMS:
  for item in rss_search(site,term)+html_search(site,term):
   if item[0] not in seen:seen.add(item[0]);found.append(item)
   if len(found)>=MAX_PRODUCTS:return found
 return found

def jsonld(html):
 vals=[];name=None
 for s in BeautifulSoup(html,'html.parser').find_all('script',type='application/ld+json'):
  try:
   o=json.loads(s.string or s.get_text()); stack=o if isinstance(o,list) else [o]
   for x in stack:
    if not isinstance(x,dict) or 'Product' not in (x.get('@type') if isinstance(x.get('@type'),list) else [x.get('@type')]):continue
    name=name or x.get('name'); offers=x.get('offers') or []; offers=offers if isinstance(offers,list) else [offers]
    for z in offers:
     if isinstance(z,dict):
      for k in ('price','lowPrice','highPrice'):
       p=price(z.get(k));
       if p is not None:vals.append(p)
  except:pass
 return name,sorted(set(vals))

def product_page(site,url,title,snip,browser):
 page=browser.new_page();page.set_default_timeout(5000);page.set_default_navigation_timeout(15000)
 try:
  r=page.goto(url,wait_until='domcontentloaded'); status=r.status if r else 0;print(f'{site} ürün HTTP: {status} | {url}')
  if not r or status>=400:
   ps=[x for x in snip if x is not None]
   if ps:
    cur=ps[0];prev=next((x for x in ps[1:] if x>cur*1.03),None)
    print(f'{site} ürün engelli; aynı arama sonucundaki fiyat kullanılıyor: {ps[:6]}')
    return {'url':url,'title':title,'current':cur,'previous':prev}
  page.wait_for_timeout(1000);html=page.content();name,jd=jsonld(html)
  if site=='Trendyol':
   sels=['meta[property="product:price:amount"]','meta[itemprop="price"]','[data-testid="price-current"]','[class*="prc-dsc"]','[class*="price-current"]']
  else:
   sels=['meta[property="product:price:amount"]','meta[itemprop="price"]','[data-test-id="price-current"]','[data-test-id="current-price"]','[class*="product-price"]','[class*="current-price"]']
  cur=None
  for sel in sels:
   try:
    loc=page.locator(sel)
    for i in range(min(loc.count(),10)):
     raw=loc.nth(i).get_attribute('content') if sel.startswith('meta') else loc.nth(i).inner_text(timeout=300);x=price(raw)
     if x is not None:cur=x;break
   except:pass
   if cur is not None:break
  if cur is None and jd:cur=jd[0]
  if cur is None:
   ps=prices(page.locator('body').inner_text(timeout=4000));cur=ps[0] if ps else None
  if cur is None:return None
  prev=next((x for x in jd if x>cur*1.03),None)
  try:name=page.locator('meta[property="og:title"]').get_attribute('content') or name
  except:pass
  return {'url':url,'title':re.sub(r'\s+',' ',name or title or 'Ürün').strip()[:300],'current':cur,'previous':prev}
 except Exception as e:print(f'{site} ürün hata: {type(e).__name__}: {e}');return None
 finally:page.close()

def history(url):
 since=(datetime.now(timezone.utc)-timedelta(days=HISTORY_DAYS)).isoformat()
 try:
  rows=sb('GET','price_history',params={'select':'price,recorded_at','product_url':f'eq.{url}','recorded_at':f'gte.{since}','order':'recorded_at.desc','limit':'100'});return [float(x['price']) for x in rows]
 except:return []

def process(p):
 now=datetime.now(timezone.utc).isoformat();old=history(p['url']);prevobs=old[0] if old else None
 rows=sb('GET','products',params={'select':'*','product_url':f'eq.{p["url"]}','limit':'1'});row=rows[0] if rows else None
 payload={'product_name':p['title'],'current_price':p['current'],'previous_price':prevobs,'product_url':p['url'],'site':p['site'],'updated_at':now}
 if row:sb('PATCH',f'products?id=eq.{row["id"]}',json=payload)
 else:row=(sb('POST','products',json=payload) or [payload])[0]
 sb('POST','price_history',json={'price':p['current'],'product_url':p['url'],'site':p['site'],'recorded_at':now})
 print(f'Kontrol: {p["site"]} | mevcut={p["current"]:.2f} | son gözlenen={prevobs or 0:.2f} | geçmiş={len(old)}')
 if prevobs is None or prevobs<=p['current']:return False
 disc=(prevobs-p['current'])/prevobs*100
 if disc<MIN_DISCOUNT:return False
 last=row.get('last_posted_at') if isinstance(row,dict) else None
 if last:
  try:
   if datetime.now(timezone.utc)-datetime.fromisoformat(last.replace('Z','+00:00'))<timedelta(hours=COOLDOWN):return False
  except:pass
 msg=f'🔥 %{disc:.0f} İNDİRİM\n\n{p["title"]}\n\n💰 {p["current"]:,.2f} TL\n🏷️ Önce: {prevobs:,.2f} TL\n🛍️ {p["site"]}\n🔗 {p["url"]}'
 r=requests.post('https://api.telegram.org/bot'+TOKEN+'/sendMessage',json={'chat_id':CHANNEL_ID,'text':msg},timeout=15);r.raise_for_status()
 sb('PATCH',f'products?id=eq.{row["id"]}',json={'last_posted_at':now});return True

def main():
 print('=== HB/Trendyol V3 keşif başladı ===');sent=0
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True,args=['--no-sandbox'])
  for site in ('Hepsiburada','Trendyol'):
   found=discover(site);print(f'{site}: {len(found)} aday')
   for url,title,snip in found:
    p=product_page(site,url,title,snip,browser)
    if p:
     p['site']=site
     try:sent+=1 if process(p) else 0
     except Exception as e:print(f'{site} işlem hata: {type(e).__name__}: {e}')
  browser.close()
 print(f'=== V3 bitti. Gönderilen: {sent} ===')
if __name__=='__main__':main()
