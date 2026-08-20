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
QUERIES=['indirim','fırsat','kampanya','çok satan','elektronik','telefon','kulaklık','televizyon','ev yaşam','mutfak','kişisel bakım','bebek','oyuncak','spor','kozmetik']
MONEY_RE=re.compile(r'(?<![A-ZÇĞİÖŞÜ])(?:\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)(?![A-ZÇĞİÖŞÜ])',re.I)
BAD_PRICE_CONTEXT=re.compile(r'(?:kupon|kod|kazan[çc]|avantaj|indirim|tasarruf|kargo|shipping|aylık|ayda|/ay|x\s*ay|taksit|puan|cashback|bonus|hediye)',re.I)

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
 try:
  x=float(s); return x if 0<x<10000000 else None
 except:return None

def prices(text,contextual=True):
 out=[]
 for m in MONEY_RE.finditer(text or ''):
  x=money(m.group())
  if not x:continue
  if contextual:
   ctx=(text[max(0,m.start()-42):min(len(text),m.end()+42)] or '')
   if BAD_PRICE_CONTEXT.search(ctx):
    continue
  out.append(x)
 return out

def normalize(site,u):
 p=urlparse(u); q=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if k.lower() not in TRACKING]
 if site=='Amazon' and AMAZON_TAG:q.append(('tag',AMAZON_TAG))
 return urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q,doseq=True),''))

def valid(site,u):
 try:
  p=urlparse(u); host=p.netloc.lower().replace('www.','')
  return host.endswith(MARKETS[site][1]) and bool(MARKETS[site][2].search(p.path))
 except:return False

def clean_title(text):
 text=' '.join((text or '').split())
 text=re.sub(r'\b(?:Sepete ekle|Hızlı Teslimat|Çok Satan|Sponsorlu|Reklam)\b',' ',text,flags=re.I)
 return re.sub(r'\s{2,}',' ',text).strip()[:220]

def extract_card_data(site,href,anchor_text,card_text):
 all_prices=prices(card_text)
 if len(all_prices)<2:return None
 current=None; previous=None
 for pat in [
  r'(?:şimdi|şuan|şu an|güncel|satış|fiyatı?)\s*[:=]?\s*([\d.,]+)\s*(?:TL|₺)',
  r'([\d.,]+)\s*(?:TL|₺)\s*(?:yerine|şimdi)',
 ]:
  m=re.search(pat,card_text,re.I)
  if m:current=money(m.group(1));break
 for pat in [
  r'(?:önceki|eski|liste|normal|normalde)\s*[:=]?\s*([\d.,]+)\s*(?:TL|₺)',
  r'([\d.,]+)\s*(?:TL|₺)\s*(?:yerine|önce)',
 ]:
  m=re.search(pat,card_text,re.I)
  if m:previous=money(m.group(1));break
 if not current:current=min(all_prices)
 if not previous:previous=max(all_prices)
 if previous<=current:return None
 disc=(previous-current)/previous*100
 if disc<MIN_DISCOUNT:return None
 title=clean_title(anchor_text)
 if len(title)<10:title=clean_title(card_text)
 if len(title)<10:return None
 return disc,normalize(site,href),title,current,previous

def extract_search_candidates(page,site,query):
 base=MARKETS[site][0]
 page.goto(base+quote(query),wait_until='domcontentloaded',timeout=12000)
 page.wait_for_timeout(1000)
 raw=page.locator('a[href]').evaluate_all("""els => els.map(a => {
  let p=a; let card='';
  for(let i=0;i<7 && p;i++,p=p.parentElement){
    let t=(p.innerText||'').replace(/\\s+/g,' ').trim();
    if((t.match(/(?:TL|₺)/gi)||[]).length>=2){card=t;break;}
  }
  return {href:a.href,text:(a.innerText||'').trim(),card};
})""")
 out=[]; seen=set()
 for item in raw:
  u=item.get('href') or ''
  if not valid(site,u):continue
  u=normalize(site,u)
  if u in seen:continue
  seen.add(u)
  data=extract_card_data(site,u,item.get('text') or '',item.get('card') or '')
  if data:
   out.append(data)
   if len(out)>=8:break
 print(f'BAĞIMSIZ ARAMA | {site} | "{query}" | aday={len(out)}')
 return out

def verify(page,site,u,fallback_title,expected_current,expected_previous):
 try:
  page.goto(u,wait_until='domcontentloaded',timeout=12000)
  page.wait_for_timeout(700)
  html=page.content(); soup=BeautifulSoup(html,'html.parser')
  title=(soup.select_one('meta[property="og:title"]') or soup.select_one('meta[name="twitter:title"]'))
  title=title.get('content','').strip() if title else ''
  if not title:title=(soup.title.get_text(' ',strip=True) if soup.title else fallback_title)
  image=(soup.select_one('meta[property="og:image"]') or soup.select_one('meta[name="twitter:image"]'))
  image=image.get('content','').strip() if image else None
  current_vals=[]; old=[]
  for sel in ['meta[property="product:price:amount"]','meta[itemprop="price"]','[itemprop="price"]','[data-price]']:
   for e in soup.select(sel):
    x=money(e.get('content') or e.get('value') or e.get('data-price') or e.get_text(' ',strip=True))
    if x:current_vals.append(x)
  for sel in ['del','s','.old-price','.list-price','.price-old','[class*="oldPrice"]','[class*="old-price"]','[class*="listPrice"]','[class*="discountedPrice"]']:
   for e in soup.select(sel):
    x=money(e.get_text(' ',strip=True))
    if x:old.append(x)
  if not current_vals:
   current_vals=prices(soup.get_text(' ',strip=True))[:50]
  if not current_vals:return None
  # Never choose a tiny coupon/installment/shipping value as the product price.
  plausible=[x for x in current_vals if x>=max(1,expected_current*0.50)]
  if not plausible: plausible=current_vals
  current=min(plausible,key=lambda x:abs(x-expected_current))
  if abs(current-expected_current)/max(expected_current,1)>0.35:return None
  previous=max(old or [x for x in current_vals if x>current],default=None)
  if not previous or previous<=current:previous=expected_previous
  if not previous or previous<=current:return None
  disc=(previous-current)/previous*100
  if disc<MIN_DISCOUNT:return None
  # Sanity guard: a 99%+ drop is allowed only when the product page itself confirms it.
  if disc>=95 and current<expected_current*0.5:return None
  return clean_title(title),current,previous,disc,image
 except Exception as e:
  print('VERIFY HATA',site,u,e); return None

def send(site,u,title,current,previous,disc,image):
 rows=sb('GET','products',params={'select':'*','product_url':f'eq.{u}','limit':'1'})
 row=rows[0] if rows else None
 last=row.get('last_posted_at') if isinstance(row,dict) else None
 if last:
  try:
   if datetime.now(timezone.utc)-datetime.fromisoformat(last.replace('Z','+00:00'))<timedelta(hours=COOLDOWN):
    print(f'BAĞIMSIZ ATLANDI | cooldown | {title[:80]}');return False
  except:pass
 fmt=lambda x:f'{x:,.2f} TL'.replace(',','X').replace('.',',').replace('X','.')
 text=f'🔥 %{disc:.0f} İNDİRİM\n\n🛍️ {title}\n💰 {fmt(current)}\n🏷️ Önceki: {fmt(previous)}\n\n👇 Fırsata git'
 kb={'inline_keyboard':[[{'text':'🛒 FIRSATA GİT','url':u}]]}
 try:
  sent=False
  if image:
   r=requests.post('https://api.telegram.org/bot'+TOKEN+'/sendPhoto',json={'chat_id':CHAT,'photo':image,'caption':text,'reply_markup':kb},timeout=15)
   sent=r.ok
  if not sent:
   r=requests.post('https://api.telegram.org/bot'+TOKEN+'/sendMessage',json={'chat_id':CHAT,'text':text,'disable_web_page_preview':False,'reply_markup':kb},timeout=15)
   r.raise_for_status()
 except Exception as e:
  print('GÖNDERME HATASI',site,u,e);return False
 now=datetime.now(timezone.utc).isoformat()
 if row and row.get('id'):
  sb('PATCH',f'products?id=eq.{row["id"]}',json={'last_posted_at':now,'current_price':current,'previous_price':previous,'updated_at':now})
 else:
  sb('POST','products',json={'product_name':title,'current_price':current,'previous_price':previous,'product_url':u,'site':site,'updated_at':now,'last_posted_at':now})
 print(f'BAĞIMSIZ FIRSAT | {site} | %{disc:.1f} | {current:.2f} TL | {title[:90]}')
 return True

def main():
 total=0; candidates_total=0; verified_total=0
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True)
  page=browser.new_page(user_agent=HEAD['User-Agent'],locale='tr-TR')
  detail=browser.new_page(user_agent=HEAD['User-Agent'],locale='tr-TR')
  for site in MARKETS:
   for query in QUERIES:
    try:
     candidates=extract_search_candidates(page,site,query); candidates_total+=len(candidates)
     for disc,u,title,current,old in candidates:
      v=verify(detail,site,u,title,current,old)
      if v:
       verified_total+=1
       t,c,p,d,img=v
       if send(site,u,t,c,p,d,img):total+=1
    except Exception as e:print('ARAMA HATA',site,query,e)
  browser.close()
 print(f'Bağımsız tarama tamamlandı | aday={candidates_total} doğrulanan={verified_total} gönderilen={total}')

if __name__=='__main__':main()
