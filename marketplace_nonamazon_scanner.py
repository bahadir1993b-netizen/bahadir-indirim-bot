import os, re, requests, json
from urllib.parse import quote, urlparse
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

TOKEN=os.environ['TELEGRAM_BOT_TOKEN']; SB=os.environ['SUPABASE_URL'].rstrip('/'); KEY=os.environ['SUPABASE_SERVICE_KEY']; CHAT='-1004424116637'
MIN_DISCOUNT=15.0; COOLDOWN=12
HEAD={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36','Accept-Language':'tr-TR,tr;q=0.9,en;q=0.8'}
SITES={'Hepsiburada':('https://www.hepsiburada.com/ara?q=',re.compile(r'-p-[A-Za-z0-9]+(?:[/?#&]|$)',re.I)),'Trendyol':('https://www.trendyol.com/sr?q=',re.compile(r'-p-\d+(?:[/?#&]|$)',re.I))}
QUERIES=['indirimli elektronik','telefon kulaklık','ev yaşam mutfak','kişisel bakım']
MONEY=re.compile(r'(?<![A-ZÇĞİÖŞÜ])(?:\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)(?![A-ZÇĞİÖŞÜ])',re.I)

def money(s):
 s=re.sub(r'[^0-9,.]','',str(s));
 if not s:return None
 if ',' in s and '.' in s:s=s.replace('.','').replace(',','.') if s.rfind(',')>s.rfind('.') else s.replace(',','')
 elif ',' in s:
  a,b=s.rsplit(',',1);s=a.replace('.','')+'.'+b if len(b)<=2 else s.replace(',','')
 elif '.' in s:
  a,b=s.rsplit('.',1);s=s.replace('.','') if len(b)>2 else s
 try:return float(s)
 except:return None

def sb(method,path,**kw):
 h={'apikey':KEY,'Authorization':f'Bearer {KEY}','Content-Type':'application/json','Accept':'application/json'}
 r=requests.request(method,f'{SB}/rest/v1/{path}',headers=h,timeout=10,**kw);r.raise_for_status();return r.json() if r.text else []

def normalize(site,url):
 p=urlparse(url);return p._replace(query='',fragment='').geturl().rstrip('/')

def already_posted(site,u):
 try:
  rows=sb('GET','products',params={'select':'last_posted_at','site':f'eq.{site}','product_url':f'eq.{u}','limit':'1'})
  if rows and rows[0].get('last_posted_at'):
   return datetime.now(timezone.utc)-datetime.fromisoformat(rows[0]['last_posted_at'].replace('Z','+00:00'))<timedelta(hours=COOLDOWN)
 except Exception:pass
 return False

def extract(page,site,query):
 base,pat=SITES[site];page.goto(base+quote(query),wait_until='domcontentloaded',timeout=10000);page.wait_for_timeout(700)
 items=page.locator('a[href]').evaluate_all("""els=>els.map(a=>{let p=a,card='';for(let i=0;i<7&&p;i++,p=p.parentElement){let t=(p.innerText||'').replace(/\\s+/g,' ').trim();if((t.match(/(?:TL|₺)/gi)||[]).length>=1){card=t;if((t.match(/(?:TL|₺)/gi)||[]).length>=2)break}}return{href:a.href,text:(a.innerText||'').trim(),card};})""")
 out=[];seen=set()
 for x in items:
  u=x.get('href') or '';card=x.get('card') or '';title=x.get('text') or ''
  if not pat.search(urlparse(u).path):continue
  u=normalize(site,u)
  if u in seen or already_posted(site,u):continue
  seen.add(u);vals=[money(m.group()) for m in MONEY.finditer(card)];vals=[v for v in vals if v]
  if len(vals)<2:continue
  current=min(vals);previous=max(vals)
  if previous<=current or previous/current>4:continue
  d=(previous-current)/previous*100
  if d<MIN_DISCOUNT:continue
  title=' '.join((title or card).split())[:220];out.append((u,title,current,previous))
  if len(out)>=5:break
 print(f'NON-AMAZON ARAMA | {site} | {query} | aday={len(out)}');return out

def verify(page,site,u,expected,previous):
 try:
  page.goto(u,wait_until='domcontentloaded',timeout=8000);page.wait_for_timeout(500);soup=BeautifulSoup(page.content(),'html.parser');vals=[]
  for el in soup.select('meta[itemprop="price"],meta[property="product:price:amount"],[itemprop="price"],[data-price]'):
   v=money(el.get('content') or el.get('value') or el.get('data-price') or el.get_text(' ',strip=True));
   if v:vals.append(v)
  for el in soup.select('script[type="application/ld+json"]'):
   for m in re.finditer(r'"price"\s*:\s*"?([0-9]+(?:[.,][0-9]+)?)',el.get_text(' ',strip=True)):
    v=money(m.group(1));
    if v:vals.append(v)
  if not vals:return None
  current=min(vals,key=lambda v:abs(v-expected))
  if abs(current-expected)/max(expected,1)>.05:print(f'NON-AMAZON FİYAT RED | {site} | arama={expected:.2f} | canlı={current:.2f}');return None
  old=previous if previous>current else None
  if not old or (old-current)/old*100<MIN_DISCOUNT:return None
  te=soup.select_one('meta[property="og:title"]');ie=soup.select_one('meta[property="og:image"]')
  title=te.get('content','').strip() if te else (soup.title.get_text(' ',strip=True) if soup.title else site)
  return title[:220],current,old,(old-current)/old*100,ie.get('content') if ie else None
 except Exception as e:print(f'NON-AMAZON VERIFY HATA | {site} | {type(e).__name__}');return None

def send(site,u,title,current,old,d,image):
 fmt=lambda x:f'{x:,.2f} TL'.replace(',','X').replace('.',',').replace('X','.')
 text=f'⭐️ BOTUN BULDUĞU FIRSAT\n\n🔥 %{d:.0f} İNDİRİM\n\n🛍️ {title}\n💰 {fmt(current)}\n🏷️ Önceki: {fmt(old)}\n\n👇 Fırsata git';kb={'inline_keyboard':[[{'text':'🛒 FIRSATA GİT','url':u}]]}
 try:
  if image:
   r=requests.get(image,headers=HEAD,timeout=8)
   if r.ok and len(r.content)>1000:
    requests.post(f'https://api.telegram.org/bot{TOKEN}/sendPhoto',data={'chat_id':CHAT,'caption':text[:1024],'reply_markup':json.dumps(kb,ensure_ascii=False)},files={'photo':('product.jpg',r.content,'image/jpeg')},timeout=15).raise_for_status();return True
  requests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage',json={'chat_id':CHAT,'text':text,'disable_web_page_preview':False,'reply_markup':kb},timeout=10).raise_for_status();return True
 except Exception as e:print(f'NON-AMAZON GÖNDERME HATA | {site} | {type(e).__name__}: {e}');return False

def main():
 sent=0
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True);page=browser.new_page(user_agent=HEAD['User-Agent'],locale='tr-TR');detail=browser.new_page(user_agent=HEAD['User-Agent'],locale='tr-TR')
  for site in SITES:
   for q in QUERIES:
    try:
     for u,title,current,old in extract(page,site,q):
      v=verify(detail,site,u,current,old)
      if v:
       t,c,p,d,img=v
       if send(site,u,t,c,p,d,img):sent+=1;print(f'⭐️ NON-AMAZON FIRSAT | {site} | %{d:.1f} | {c:.2f} TL | {t[:80]}')
    except Exception as e:print(f'NON-AMAZON ARAMA HATA | {site} | {q} | {type(e).__name__}: {e}')
  browser.close()
 print(f'NON-AMAZON TARAMA TAMAMLANDI | gönderilen={sent}')
if __name__=='__main__':main()
