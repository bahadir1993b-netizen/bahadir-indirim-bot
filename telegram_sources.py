import os
import re
import html as htmlmod
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

TOKEN=os.environ['TELEGRAM_BOT_TOKEN']; SB=os.environ['SUPABASE_URL'].rstrip('/'); KEY=os.environ['SUPABASE_SERVICE_KEY']; CHAT='-1004424116637'
if SB.endswith('/rest/v1'): SB=SB[:-8].rstrip('/')
MAX_AGE=30
MIN_DISCOUNT=6.0
COOLDOWN=12
AMAZON_TAG=os.getenv('AMAZON_ASSOCIATE_TAG','').strip()
HEAD={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36','Accept-Language':'tr-TR,tr;q=0.9,en;q=0.8'}
SOURCES={'OnuAl':'onual_firsat','EnesOzen':'enesozen','OzelFirsatlar':'ozelfirsat','AmazonOzel':'amazonozel','FirsatZ':'firsatz','FirsatMerkezi':'firsatmerkez','IndirimDeal':'indirimdeal'}
MARKET={'amazon.com.tr':'Amazon','hepsiburada.com':'Hepsiburada','trendyol.com':'Trendyol'}
SHORT={'app.hb.biz':'Hepsiburada','hps.im':'Hepsiburada','ty.gl':'Trendyol','tyml.gl':'Trendyol','amzn.to':'Amazon','amzn.eu':'Amazon','link.amazon':'Amazon','onu.al':None}
TRACKING={'utm_source','utm_medium','utm_campaign','utm_content','utm_term','fbclid','gclid','ref','ref_','tag','ascsubtag','linkcode','creative','creativeasin','camp','adid','dib','dib_tag','pd_rd_i','pd_rd_r','pd_rd_w','pd_rd_wg','pf_rd_i','pf_rd_m','pf_rd_p','pf_rd_r','pf_rd_s','pf_rd_t','_encoding','aff_fcid','aff_fsk','aff_platform','aff_trace_key','spm','partner_id'}
DEAL_WORDS=re.compile(r'kupon|kod(?:u)?|sepette|kampanya|indirim|promosyon|aktif|geçerli|gecerli|fırsat|yerine|şimdi|simdi|2 al 1|3 al 2|4 al 3',re.I)
MONEY_RE=re.compile(r'(?<![A-ZÇĞİÖŞÜ])(?:\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)(?![A-ZÇĞİÖŞÜ])',re.I)

def sb(method,path,**kw):
 h={'apikey':KEY,'Authorization':f'Bearer {KEY}','Content-Type':'application/json','Accept':'application/json'}
 if method=='POST':h['Prefer']='return=representation'
 r=requests.request(method,f'{SB}/rest/v1/{path}',headers=h,timeout=15,**kw); r.raise_for_status(); return r.json() if r.text else []

def clean(u): return htmlmod.unescape(u or '').replace('\\/','/').split('#',1)[0].rstrip('/')

def money(s):
 s=re.sub(r'[^0-9,.]','',str(s).replace('TL','').replace('₺','').replace(' ',''))
 if not s:return None
 if ',' in s and '.' in s:s=s.replace('.','').replace(',','.') if s.rfind(',')>s.rfind('.') else s.replace(',','')
 elif ',' in s:
  a,b=s.rsplit(',',1); s=a.replace('.','')+'.'+b if len(b)<=2 else s.replace(',','')
 elif '.' in s:
  a,b=s.rsplit('.',1); s=s.replace('.','') if len(b)>2 else s
 try:
  x=float(s); return x if 0<x<10000000 else None
 except:return None

def prices(t): return [money(m.group()) for m in MONEY_RE.finditer(t or '') if money(m.group()) is not None]

def source_pair(t):
 for pat in [r'(\d[\d.,]*)\s*(?:TL|₺)\s+yerine\s+(\d[\d.,]*)\s*(?:TL|₺)',r'(?:önceki|onceki|eski|liste|normal|normalde)\s*[:=]?\s*(\d[\d.,]*)\s*(?:TL|₺).*?(?:şuan|şu an|simdi|şimdi|yeni)\s*[:=]?\s*(\d[\d.,]*)\s*(?:TL|₺)']:
  m=re.search(pat,t or '',re.I|re.S)
  if m:
   a,b=money(m.group(1)),money(m.group(2))
   if a and b:return (b,a) if a>b else (a,b)
 p=prices(t); return (p[0],None) if p else (None,None)

def coupon_savings(t,current):
 if not current:return 0,None
 m=re.search(r'(\d[\d.,]*)\s*(?:TL|₺)\s*(?:indirim|avantaj|kazanç|kazanc)',t or '',re.I)
 if m:
  amt=money(m.group(1))
  if amt and 0<amt<current:return amt,current-amt
 m=re.search(r'%\s*(\d{1,2}(?:[.,]\d+)?)\s*(?:indirim|kupon|kod)',t or '',re.I)
 if m:
  pct=float(m.group(1).replace(',','.'))
  if 0<pct<100:return current*pct/100,current*(1-pct/100)
 return 0,None

def site(u):
 h=urlparse(u).netloc.lower().replace('www.','')
 if h in MARKET:return MARKET[h]
 for k,v in SHORT.items():
  if h==k or h.endswith('.'+k):return v
 return None

def valid(s,u):
 p=urlparse(u); h=p.netloc.lower().replace('www.',''); path=p.path
 if s=='Amazon':return h.endswith('amazon.com.tr') and bool(re.search(r'/(?:dp|gp/product)/[A-Z0-9]{8,}',path,re.I))
 if s=='Hepsiburada':return h.endswith('hepsiburada.com') and bool(re.search(r'-p-[A-Za-z0-9]+(?:[/?#&]|$)',path,re.I))
 if s=='Trendyol':return h.endswith('trendyol.com') and bool(re.search(r'-p-\d+(?:[/?#&]|$)',path,re.I))
 return False

def normalize(s,u):
 if not u or s not in MARKET.values():return None
 p=urlparse(clean(u)); q=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if k.lower() not in TRACKING]
 if s=='Amazon' and AMAZON_TAG:q.append(('tag',AMAZON_TAG))
 return urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q,doseq=True),''))

def tokens(text):
 stop={'ürün','ürünü','hızlı','fırsat','indirim','adet','parça','set','marka','model','yeni','şimdi','tl','sadece','stok','kampanya','sepette'}
 return {x for x in re.findall(r'[a-zçğıöşü0-9]{3,}',(text or '').lower()) if x not in stop}

def title_score(title,candidate_text):
 a,b=tokens(title),tokens(candidate_text); return len(a&b)/max(1,len(a)) if a and b else 0

def http_resolve(u,s):
 try:
  r=requests.get(clean(u),headers=HEAD,timeout=5,allow_redirects=True); f=clean(r.url); ss=site(f)
  if ss and valid(ss,f):return normalize(ss,f)
  soup=BeautifulSoup(r.text,'html.parser')
  for a in soup.select('a[href]'):
   h=clean(a.get('href') or '')
   if site(h)==s and valid(s,h):return normalize(s,h)
 except:pass
 return None

def search_marketplace_http(s,title):
 base={'Amazon':'https://www.amazon.com.tr/s?k=','Hepsiburada':'https://www.hepsiburada.com/ara?q=','Trendyol':'https://www.trendyol.com/sr?q='}.get(s)
 if not base or not title:return None
 try:
  r=requests.get(base+requests.utils.quote(' '.join(title.split())[:140]),headers=HEAD,timeout=6)
  if r.status_code>=400:return None
  soup=BeautifulSoup(r.text,'html.parser'); candidates=[]
  for a in soup.select('a[href]'):
   u=clean(a.get('href') or '')
   if valid(s,u):candidates.append((title_score(title,(a.get_text(' ',strip=True) or '')[:500]+' '+u),u))
  if candidates:
   score,u=max(candidates,key=lambda x:x[0])
   if score>=0.25:return normalize(s,u)
 except:pass
 return None

def search_marketplace_browser(page,s,title):
 base={'Amazon':'https://www.amazon.com.tr/s?k=','Hepsiburada':'https://www.hepsiburada.com/ara?q=','Trendyol':'https://www.trendyol.com/sr?q='}.get(s)
 if not base:return None
 try:
  page.goto(base+requests.utils.quote(' '.join(title.split())[:140]),wait_until='domcontentloaded',timeout=9000); candidates=[]
  for a in page.locator('a[href]').all():
   u=clean(a.get_attribute('href') or '')
   if valid(s,u):candidates.append((title_score(title,(a.inner_text() or '')[:500]+' '+u),u))
  if candidates:
   score,u=max(candidates,key=lambda x:x[0])
   if score>=0.25:return normalize(s,u)
 except:pass
 return None

def resolve(page,u,s,title): return http_resolve(u,s) or search_marketplace_http(s,title) or search_marketplace_browser(page,s,title)

def marketplace_price_check(s,u,expected):
 try:
  r=requests.get(u,headers=HEAD,timeout=7)
  if r.status_code>=400:return None,None
  soup=BeautifulSoup(r.text,'html.parser'); vals=[]
  for sel in ['meta[property="product:price:amount"]','meta[itemprop="price"]','[itemprop="price"]','[data-price]']:
   for el in soup.select(sel):
    x=money(el.get('content') or el.get('value') or el.get_text(' ',strip=True) or el.get('data-price'))
    if x:vals.append(x)
  for el in soup.select('del,s,.old-price,.list-price,.price-old,[class*="oldPrice"],[class*="old-price"],[class*="listPrice"]'):
   x=money(el.get_text(' ',strip=True))
   if x:vals.append(x)
  if not vals or not expected:return None,None
  current=min(vals,key=lambda x:abs(x-expected))
  if abs(current-expected)/max(expected,1)>0.35:return None,None
  return current,max((x for x in vals if x>current),default=None)
 except:return None,None

def coupon_code(text):
 pats=[r'\b(?:KOD|KODU|KUPON|KUPON KODU|PROMOSYON(?: KODU)?)\s*[:=\-]?\s*([A-ZÇĞİÖŞÜ0-9][A-ZÇĞİÖŞÜ0-9_-]{3,23})\b',r'\b([A-ZÇĞİÖŞÜ0-9][A-ZÇĞİÖŞÜ0-9_-]{4,23})\s+(?:KOD(?:U)?|KUPON(?:U)?)\b']
 for pat in pats:
  for m in re.finditer(pat,text or '',re.I):
   code=m.group(1).upper()
   if code.isdigit() or not re.search(r'[A-ZÇĞİÖŞÜ]',code) or code in {'INDIRIM','KAMPANYA','FIRSAT','AMAZON','HEPSIBURADA','TRENDYOL'}:continue
   return code
 return None

def seen(k):return bool(sb('GET','price_history',params={'select':'recorded_at','product_url':f'eq.telegram://{k}','limit':'1'}))
def remember(k):sb('POST','price_history',json={'price':0,'product_url':f'telegram://{k}','site':'telegram','recorded_at':datetime.now(timezone.utc).isoformat()})

def save(s,u,t,c,p):
 now=datetime.now(timezone.utc).isoformat(); rows=sb('GET','products',params={'select':'*','product_url':f'eq.{u}','limit':'1'})
 payload={'product_name':t,'current_price':c,'previous_price':p,'product_url':u,'site':s,'updated_at':now}
 if rows:sb('PATCH',f'products?id=eq.{rows[0]["id"]}',json=payload);return rows[0]
 return (sb('POST','products',json=payload) or [payload])[0]

def send(s,u,t,c,p,source,post_id,signal,coupon=None):
 if not valid(s,u):print(f'ATLANDI | {source}:{post_id} | geçersiz link');return False
 key=f'{source}:{post_id}'
 if seen(key):return False
 disc=(p-c)/p*100 if p and p>c else None
 if disc is not None and disc<MIN_DISCOUNT:print(f'ATLANDI | {source}:{post_id} | %{disc:.1f} < %{MIN_DISCOUNT}');remember(key);return False
 if disc is None and not coupon and not DEAL_WORDS.search(signal or ''):print(f'ATLANDI | {source}:{post_id} | kampanya sinyali yok');remember(key);return False
 row=save(s,u,t,c,p); last=row.get('last_posted_at') if isinstance(row,dict) else None
 if last:
  try:
   if datetime.now(timezone.utc)-datetime.fromisoformat(last.replace('Z','+00:00'))<timedelta(hours=COOLDOWN):print(f'ATLANDI | {source}:{post_id} | cooldown');remember(key);return False
  except:pass
 lines=[f'🔥 %{disc:.0f} İNDİRİM' if disc is not None else ('🎟️ KUPONLU FIRSAT' if coupon else '🔥 FIRSAT'),' ',f'🛍️ {t}',f'💰 {c:,.2f} TL'.replace(',','X').replace('.',',').replace('X','.')]
 if p and p>c:lines.append(f'🏷️ Önceki: {p:,.2f} TL'.replace(',','X').replace('.',',').replace('X','.'))
 if coupon:lines.append(f'🎟️ Kupon: {coupon}')
 lines+=['','👇 Fırsata git']
 payload={'chat_id':CHAT,'text':'\n'.join(lines),'disable_web_page_preview':False,'reply_markup':{'inline_keyboard':[[{'text':'🛒 FIRSATA GİT','url':u}]]}}
 requests.post('https://api.telegram.org/bot'+TOKEN+'/sendMessage',json=payload,timeout=15).raise_for_status()
 if isinstance(row,dict) and row.get('id'):sb('PATCH',f'products?id=eq.{row["id"]}',json={'last_posted_at':datetime.now(timezone.utc).isoformat()})
 remember(key); print(f'GÖNDERİLDİ | {s} | {c:.2f} TL'+(f' | %{disc:.1f}' if disc is not None else '')); return True

def extract_title(raw):
 parts=[x.strip(' -•') for x in re.split(r'\s{2,}|\n',raw) if x.strip()]
 for p in parts:
  if len(p)>12 and not re.fullmatch(r'.*(TL|₺|KOD|KUPON).*',p,re.I):return p[:180]
 return raw[:180]

def process(source,b,page):
 tx=b.select_one('.tgme_widget_message_text')
 if not tx:return False
 raw=tx.get_text(' ',strip=True); links=[clean(a.get('href') or '') for a in b.select('a[href]')]
 s=next((site(x) for x in links if site(x)),None) or next((x for x in MARKET.values() if re.search(r'\b'+re.escape(x)+r'\b',raw,re.I)),None)
 if not s:return False
 current,old=source_pair(raw)
 if not current:return False
 saving,effective=coupon_savings(raw,current); coupon=coupon_code(raw)
 if effective and effective<current:current=effective
 if old is None and links:
  direct=next((normalize(s,x) for x in links if valid(s,x)),None)
  if direct:
   mc,mo=marketplace_price_check(s,direct,source_pair(raw)[0])
   if mc and abs(mc-current)/max(current,1)<0.35:current=mc
   if mo and mo>current:old=mo
 u=next((normalize(s,x) for x in links if valid(s,x)),None)
 if not u:u=resolve(page,links[0] if links else '',s,extract_title(raw))
 if not u:return False
 if old is None and saving>0:old=current+saving
 return send(s,u,extract_title(raw),current,old,source,b.get('data-post','').split('/')[-1],raw,coupon)

def fetch_source(item):
 source,channel=item
 try:return source,requests.get(f'https://t.me/s/{channel}',headers=HEAD,timeout=8)
 except Exception as e:print(f'Telegram kaynak hata {source}: {type(e).__name__}: {e}');return source,None

def main():
 print(f'=== Telegram fırsat keşfi başladı | eşik=%{MIN_DISCOUNT} | yaş={MAX_AGE} dk ==='); fetched=[]
 with ThreadPoolExecutor(max_workers=len(SOURCES)) as ex:
  for f in as_completed([ex.submit(fetch_source,x) for x in SOURCES.items()]):
   source,r=f.result(); blocks=[]
   if r and r.status_code<400:
    now=datetime.now(timezone.utc)
    for b in BeautifulSoup(r.text,'html.parser').select('.tgme_widget_message'):
     tm=b.select_one('time[datetime]');tx=b.select_one('.tgme_widget_message_text')
     if not tm or not tx:continue
     try:dt=datetime.fromisoformat(tm['datetime'].replace('Z','+00:00'))
     except:continue
     age=(now-dt).total_seconds()/60
     if 0<=age<=MAX_AGE:blocks.append(b)
   print(f'Telegram kaynak {source}: HTTP {r.status_code if r else "HATA"} yeni mesaj={len(blocks)}');fetched.append((source,blocks))
 total=sum(len(x[1]) for x in fetched);sent=0
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True);page=browser.new_page()
  for source,blocks in fetched:
   for b in blocks:
    try:
     if process(source,b,page):sent+=1
    except Exception as e:print(f'ADAY HATA | {source}: {type(e).__name__}: {e}')
  browser.close()
 print(f'=== Bitti. Mesaj={total} Gönderilen={sent} Eşik=%{MIN_DISCOUNT} ===')

if __name__=='__main__':main()