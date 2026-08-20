import os
import re
import requests
from datetime import datetime, timezone, timedelta
from urllib.parse import quote, urlparse, urlunparse, parse_qsl, urlencode
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

TOKEN=os.environ['TELEGRAM_BOT_TOKEN']
SB=os.environ['SUPABASE_URL'].rstrip('/')
KEY=os.environ['SUPABASE_SERVICE_KEY']
CHAT='-1004424116637'
MIN_DISCOUNT=6.0
COOLDOWN=12
AMAZON_TAG=os.getenv('AMAZON_ASSOCIATE_TAG','').strip()
HEAD={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36','Accept-Language':'tr-TR,tr;q=0.9,en;q=0.8'}
TRACKING={'utm_source','utm_medium','utm_campaign','utm_content','utm_term','fbclid','gclid','ref','ref_','tag','ascsubtag','linkcode','creative','creativeasin','camp','adid','dib','dib_tag','pd_rd_i','pd_rd_r','pd_rd_w','pd_rd_wg','pf_rd_i','pf_rd_m','pf_rd_p','pf_rd_r','pf_rd_s','pf_rd_t','_encoding','aff_fcid','aff_fsk','aff_platform','aff_trace_key','spm','partner_id'}
MARKETS={
 'Amazon':('https://www.amazon.com.tr/s?k=','amazon.com.tr',re.compile(r'/(?:dp|gp/product)/[A-Z0-9]{8,}',re.I)),
 'Hepsiburada':('https://www.hepsiburada.com/ara?q=','hepsiburada.com',re.compile(r'-p-[A-Za-z0-9]+(?:[/?#&]|$)',re.I)),
 'Trendyol':('https://www.trendyol.com/sr?q=','trendyol.com',re.compile(r'-p-\d+(?:[/?#&]|$)',re.I)),
}
QUERIES=['elektronik','telefon aksesuar','ev yaşam','kişisel bakım','mutfak','bebek çocuk']
MONEY_RE=re.compile(r'(?<![A-ZÇĞİÖŞÜ])(?:\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)(?![A-ZÇĞİÖŞÜ])',re.I)

def sb(method,path,**kw):
 h={'apikey':KEY,'Authorization':f'Bearer {KEY}','Content-Type':'application/json','Accept':'application/json'}
 if method=='POST':h['Prefer']='return=representation'
 r=requests.request(method,f'{SB}/rest/v1/{path}',headers=h,timeout=15,**kw); r.raise_for_status(); return r.json() if r.text else []

def money(x):
 s=re.sub(r'[^0-9,.]','',str(x))
 if not s:return None
 if ',' in s and '.' in s:s=s.replace('.','').replace(',','.') if s.rfind(',')>s.rfind('.') else s.replace(',','')
 elif ',' in s:
  a,b=s.rsplit(',',1); s=a.replace('.','')+'.'+b if len(b)<=2 else s.replace(',','')
 elif '.' in s:
  a,b=s.rsplit('.',1); s=s.replace('.','') if len(b)>2 else s
 try:return float(s)
 except:return None

def prices(text):return [money(x.group()) for x in MONEY_RE.finditer(text or '') if money(x.group())]

def normalize(site,u):
 p=urlparse(u); q=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if k.lower() not in TRACKING]
 if site=='Amazon' and AMAZON_TAG:q.append(('tag',AMAZON_TAG))
 return urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q,doseq=True),''))

def valid(site,u):
 p=urlparse(u); host=p.netloc.lower().replace('www.',''); return host.endswith(MARKETS[site][1]) and bool(MARKETS[site][2].search(p.path))

def title_score(title,text):
 a=set(re.findall(r'[a-zçğıöşü0-9]{3,}',title.lower())); b=set(re.findall(r'[a-zçğıöşü0-9]{3,}',text.lower())); return len(a&b)/max(1,len(a))

def extract_search_candidates(page,site,query):
 base=MARKETS[site][0]
 page.goto(base+quote(query),wait_until='domcontentloaded',timeout=12000)
 page.wait_for_timeout(1200)
 out=[]; seen=set()
 for a in page.locator('a[href]').all():
  u=a.get_attribute('href') or ''
  if u.startswith('/'):
   u='https://'+MARKETS[site][1]+u
  if not valid(site,u):continue
  u=normalize(site,u)
  if u in seen:continue
  seen.add(u)
  text=' '.join((a.inner_text() or '').split())
  parent=text
  try:
   parent=' '.join((a.locator('xpath=..').inner_text() or '').split())
  except:pass
  ps=prices(parent)
  if len(ps)<2:continue
  current=min(ps); old=max(ps)
  if old<=current:continue
  disc=(old-current)/old*100
  if disc<MIN_DISCOUNT:continue
  title=text[:220] or parent[:220]
  if len(title)<8:title=parent[:220]
  out.append((disc,site,u,title,current,old))
  if len(out)>=4:break
 return out

def verify(page,site,u,fallback_title,expected):
 try:
  page.goto(u,wait_until='domcontentloaded',timeout=12000); page.wait_for_timeout(900)
  html=page.content(); soup=BeautifulSoup(html,'html.parser')
  title=(soup.select_one('meta[property="og:title"]') or soup.select_one('meta[name="twitter:title"]'))
  title=title.get('content','').strip() if title else ''
  if not title:title=(soup.title.get_text(' ',strip=True) if soup.title else fallback_title)
  image=(soup.select_one('meta[property="og:image"]') or soup.select_one('meta[name="twitter:image"]'))
  image=image.get('content','').strip() if image else None
  cur=[]; old=[]
  for sel in ['meta[property="product:price:amount"]','meta[itemprop="price"]','[itemprop="price"]','[data-price]']:
   for e in soup.select(sel):
    x=money(e.get('content') or e.get('value') or e.get('data-price') or e.get_text(' ',strip=True));
    if x:cur.append(x)
  for sel in ['del','s','.old-price','.list-price','.price-old','[class*="oldPrice"]','[class*="old-price"]','[class*="listPrice"]']:
   for e in soup.select(sel):
    x=money(e.get_text(' ',strip=True));
    if x:old.append(x)
  if not cur:return None
  current=min(cur,key=lambda x:abs(x-expected))
  previous=max(old or [x for x in cur if x>current],default=None)
  if not previous or previous<=current:return None
  disc=(previous-current)/previous*100
  if disc<MIN_DISCOUNT:return None
  return title[:220],current,previous,disc,image
 except Exception as e:
  print('VERIFY HATA',site,u,e); return None

def save(site,u,title,current,previous):
 rows=sb('GET','products',params={'select':'*','product_url':f'eq.{u}','limit':'1'})
 payload={'product_name':title,'current_price':current,'previous_price':previous,'product_url':u,'site':site,'updated_at':datetime.now(timezone.utc).isoformat()}
 if rows:
  row=rows[0]; sb('PATCH',f'products?id=eq.{row["id"]}',json=payload); return row
 return (sb('POST','products',json=payload) or [payload])[0]

def send(site,u,title,current,previous,disc,image):
 row=save(site,u,title,current,previous); last=row.get('last_posted_at') if isinstance(row,dict) else None
 if last:
  try:
   if datetime.now(timezone.utc)-datetime.fromisoformat(last.replace('Z','+00:00'))<timedelta(hours=COOLDOWN):return False
  except:pass
 fmt=lambda x:f'{x:,.2f} TL'.replace(',','X').replace('.',',').replace('X','.')
 text=f'🔥 %{disc:.0f} İNDİRİM\n\n🛍️ {title}\n💰 {fmt(current)}\n🏷️ Önceki: {fmt(previous)}\n\n👇 Fırsata git'
 kb={'inline_keyboard':[[{'text':'🛒 FIRSATA GİT','url':u}]]}
 if image:
  try:
   requests.post('https://api.telegram.org/bot'+TOKEN+'/sendPhoto',json={'chat_id':CHAT,'photo':image,'caption':text,'reply_markup':kb},timeout=15).raise_for_status()
  except: image=None
 if not image:
  requests.post('https://api.telegram.org/bot'+TOKEN+'/sendMessage',json={'chat_id':CHAT,'text':text,'disable_web_page_preview':False,'reply_markup':kb},timeout=15).raise_for_status()
 if isinstance(row,dict) and row.get('id'):sb('PATCH',f'products?id=eq.{row["id"]}',json={'last_posted_at':datetime.now(timezone.utc).isoformat()})
 print(f'BAĞIMSIZ FIRSAT | {site} | %{disc:.1f} | {current:.2f} TL | {title[:90]}'); return True

def main():
 total=0
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True)
  page=browser.new_page(user_agent=HEAD['User-Agent'],locale='tr-TR')
  detail=browser.new_page(user_agent=HEAD['User-Agent'],locale='tr-TR')
  for site in MARKETS:
   for query in QUERIES:
    try:
     candidates=extract_search_candidates(page,site,query)
     for disc,site2,u,title,current,old in candidates:
      v=verify(detail,site2,u,title,current)
      if v:
       t,c,p,d,img=v
       if send(site2,u,t,c,p,d,img):total+=1
    except Exception as e:print('ARAMA HATA',site,query,e)
  browser.close()
 print(f'Bağımsız tarama tamamlandı | gönderilen={total}')

if __name__=='__main__':main()
