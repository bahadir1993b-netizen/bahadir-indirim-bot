import os, re, html as htmlmod, requests, json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from bs4 import BeautifulSoup

TOKEN=os.environ['TELEGRAM_BOT_TOKEN']; SB=os.environ['SUPABASE_URL'].rstrip('/'); KEY=os.environ['SUPABASE_SERVICE_KEY']; CHAT='-1004424116637'
MAX_AGE=45; MIN_DISCOUNT=15.0; AMAZON_TAG=os.getenv('AMAZON_ASSOCIATE_TAG','').strip()
HEAD={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36','Accept-Language':'tr-TR,tr;q=0.9,en;q=0.8'}
SOURCES={'OnuAl':'onual_firsat','EnesOzen':'enesozen','OzelFirsatlar':'ozelfirsat','AmazonOzel':'amazonozel','FirsatZ':'firsatz','FirsatMerkezi':'firsatmerkez','IndirimDeal':'indirimdeal'}
MARKET={'amazon.com.tr':'Amazon','hepsiburada.com':'Hepsiburada','trendyol.com':'Trendyol'}
SHORT={'app.hb.biz':'Hepsiburada','hps.im':'Hepsiburada','ty.gl':'Trendyol','tyml.gl':'Trendyol','amzn.to':'Amazon','amzn.eu':'Amazon','link.amazon':'Amazon'}
MONEY_RE=re.compile(r'(?<![A-ZÇĞİÖŞÜ])(?:\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)(?![A-ZÇĞİÖŞÜ])',re.I)
DEAL_WORDS=re.compile(r'kupon|kod(?:u)?|sepette|kampanya|indirim|promosyon|aktif|geçerli|gecerli|f[ıi]rsat|yerine|şimdi|simdi|\b[234]\s*al\s*[123]\b',re.I)

def clean(u): return htmlmod.unescape(u or '').replace('\\/','/').split('#',1)[0].rstrip('/')
def money(s):
 s=re.sub(r'[^0-9,.]','',str(s).replace('TL','').replace('₺','').replace(' ',''))
 if not s:return None
 if ',' in s and '.' in s:s=s.replace('.','').replace(',','.') if s.rfind(',')>s.rfind('.') else s.replace(',','')
 elif ',' in s:
  a,b=s.rsplit(',',1); s=a.replace('.','')+'.'+b if len(b)<=2 else s.replace(',','')
 elif '.' in s:
  a,b=s.rsplit('.',1); s=s.replace('.','') if len(b)>2 else s
 try:return float(s)
 except:return None
def prices(t):return [money(x.group()) for x in MONEY_RE.finditer(t or '') if money(x.group())]
def site(u):
 h=urlparse(clean(u)).netloc.lower().replace('www.','')
 if h in MARKET:return MARKET[h]
 for k,v in SHORT.items():
  if h==k or h.endswith('.'+k):return v
 return None
def valid(s,u):
 p=urlparse(clean(u));h=p.netloc.lower().replace('www.','');path=p.path
 if s=='Amazon':return h.endswith('amazon.com.tr') and bool(re.search(r'/(?:dp|gp/product)/[A-Z0-9]{8,}',path,re.I))
 if s=='Hepsiburada':return h.endswith('hepsiburada.com') and bool(re.search(r'-p-[A-Za-z0-9]+(?:[/?#&]|$)',path,re.I))
 if s=='Trendyol':return h.endswith('trendyol.com') and bool(re.search(r'-p-\d+(?:[/?#&]|$)',path,re.I))
 return False
def normalize(s,u):
 p=urlparse(clean(u)); q=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if k.lower() not in {'tag','utm_source','utm_medium','utm_campaign','ref','ascsubtag','linkcode','creative','creativeasin','gclid','fbclid'}]
 if s=='Amazon' and AMAZON_TAG:q.append(('tag',AMAZON_TAG))
 return urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q,doseq=True),''))
def resolve(u,s):
 try:
  r=requests.get(clean(u),headers=HEAD,timeout=5,allow_redirects=True); f=clean(r.url)
  if valid(s,f):return normalize(s,f)
  soup=BeautifulSoup(r.text,'html.parser')
  for a in soup.select('a[href]'):
   h=clean(a.get('href') or '')
   if valid(s,h):return normalize(s,h)
 except Exception:pass
 return None
def sb(method,path,**kw):
 h={'apikey':KEY,'Authorization':f'Bearer {KEY}','Content-Type':'application/json','Accept':'application/json'}
 r=requests.request(method,f'{SB}/rest/v1/{path}',headers=h,timeout=10,**kw);r.raise_for_status();return r.json() if r.text else []
def seen(k):
 try:return bool(sb('GET','price_history',params={'select':'recorded_at','product_url':f'eq.telegram://{k}','limit':'1'}))
 except:return False
def remember(k):
 try:sb('POST','price_history',json={'price':0,'product_url':f'telegram://{k}','site':'telegram','recorded_at':datetime.now(timezone.utc).isoformat()})
 except:pass
def title(raw):
 x=re.sub(r'(?i)\s*👉?\s*FIRSATA\s*G[İI]T.*$','',raw or '')
 x=re.sub(r'(?i)\s*(?:#\w+|@\w+)\b',' ',x)
 x=re.split(r'\s*(?:🏷️|💰|\b\d[\d.,]*\s*(?:TL|₺))\b',x,maxsplit=1,flags=re.I)[0]
 return re.sub(r'\s+',' ',x).strip(' -•👉')[:180] or (raw or '')[:180]
def norm_title(x):return set(re.findall(r'[a-zçğıöşü0-9]{3,}',(x or '').lower()))
def duplicate_product(s,t,c):
 try:
  rows=sb('GET','products',params={'select':'product_name,current_price,last_posted_at,site','site':f'eq.{s}','order':'updated_at.desc','limit':'100'}); now=datetime.now(timezone.utc); a=norm_title(t)
  for r in rows:
   if not r.get('last_posted_at'):continue
   try:age=now-datetime.fromisoformat(r['last_posted_at'].replace('Z','+00:00'))
   except:continue
   if age>=timedelta(hours=12):continue
   old=float(r.get('current_price') or 0)
   if not old or abs(old-c)/max(old,c,1)>0.01:continue
   b=norm_title(r.get('product_name')); overlap=len(a&b)/max(1,min(len(a),len(b)))
   if overlap>=0.72:return True
 except:pass
 return False
def source_image(b):
 w=b.select_one('.tgme_widget_message_photo_wrap')
 if w:
  st=w.get('style',''); m=re.search(r"url\(['\"]?([^'\")]+)",st)
  if m:return clean(m.group(1))
  href=clean(w.get('href') or '')
  if href:
   try:
    r=requests.get(href,headers={**HEAD,'Referer':'https://t.me/'},timeout=8)
    if r.ok:
     soup=BeautifulSoup(r.text,'html.parser')
     for sel in ['meta[property="og:image"]','meta[name="twitter:image"]']:
      el=soup.select_one(sel)
      if el and el.get('content'):return clean(el.get('content'))
     m=re.search(r'https://cdn\d+\.telesco\.pe/file/[^\"\'<> )]+',r.text)
     if m:return m.group(0)
   except:pass
 im=b.select_one('.tgme_widget_message_photo img, .tgme_widget_message_photo_wrap img')
 return clean(im.get('src') or im.get('data-src') or '') if im else None
def send_photo_or_text(text,u,image,source,post_id):
 markup={'inline_keyboard':[[{'text':'🛒 FIRSATA GİT','url':u}]]}
 if image:
  try:
   ir=requests.get(image,headers={**HEAD,'Referer':'https://t.me/'},timeout=8)
   if ir.ok and len(ir.content)>1000:
    requests.post('https://api.telegram.org/bot'+TOKEN+'/sendPhoto',data={'chat_id':CHAT,'caption':text[:1024],'reply_markup':json.dumps(markup,ensure_ascii=False)},files={'photo':('source.jpg',ir.content,'image/jpeg')},timeout=15).raise_for_status(); print(f'GÖRSEL GÖNDERİLDİ | {source}:{post_id}');return
  except Exception as e:print(f'GÖRSEL HATA | {source}:{post_id} | {type(e).__name__}: {e}')
 requests.post('https://api.telegram.org/bot'+TOKEN+'/sendMessage',json={'chat_id':CHAT,'text':text,'disable_web_page_preview':False,'reply_markup':markup},timeout=10).raise_for_status()
def process(source,b):
 tx=b.select_one('.tgme_widget_message_text')
 if not tx:return False
 raw=tx.get_text(' ',strip=True);post_id=(b.get('data-post') or '').split('/')[-1];key=f'{source}:{post_id}'
 if not post_id or seen(key):return False
 links=[clean(a.get('href') or '') for a in b.select('a[href]')]; s=next((site(x) for x in links if site(x)),None) or next((x for x in MARKET.values() if re.search(r'\b'+re.escape(x)+r'\b',raw,re.I)),None)
 if not s:return False
 p=prices(raw)
 if not p:return False
 c=p[0];old=p[1] if len(p)>1 and p[1]>c else None;disc=(old-c)/old*100 if old else None
 if disc is not None and disc<MIN_DISCOUNT:return False
 if disc is None and not DEAL_WORDS.search(raw):return False
 u=None
 for x in links:
  if valid(s,x):u=normalize(s,x);break
  if site(x)==s:
   u=resolve(x,s)
   if u:break
 if not u:return False
 t=title(raw)
 if duplicate_product(s,t,c):remember(key);return False
 lines=[f'🔥 %{disc:.0f} İNDİRİM' if disc is not None else '🔥 FIRSAT','',f'🛍️ {t}',f'💰 {c:,.2f} TL'.replace(',','X').replace('.',',').replace('X','.')]
 if old and old>c:lines.append(f'🏷️ Önceki: {old:,.2f} TL'.replace(',','X').replace('.',',').replace('X','.'))
 text='\n'.join(lines+['','👇 Fırsata git'])
 try:send_photo_or_text(text,u,source_image(b),source,post_id); remember(key); print(f'⚡ HIZLI GÖNDERİLDİ | {s} | {t} | {c:.2f} TL');return True
 except Exception as e:print(f'HIZLI GÖNDERİ HATA | {source}:{post_id} | {type(e).__name__}: {e}');return False
def main():
 print('=== HIZLI TELEGRAM KAYNAK TARAMASI: önce kanallar ===');fetched=[]
 with ThreadPoolExecutor(max_workers=len(SOURCES)) as ex:
  jobs={ex.submit(requests.get,f'https://t.me/s/{ch}',headers=HEAD,timeout=8):src for src,ch in SOURCES.items()}
  for f in as_completed(jobs):
   src=jobs[f]
   try:r=f.result()
   except Exception as e:print(f'KAYNAK HATA | {src}: {type(e).__name__}');continue
   blocks=[]
   if r.status_code<400:
    now=datetime.now(timezone.utc)
    for b in BeautifulSoup(r.text,'html.parser').select('.tgme_widget_message'):
     tm=b.select_one('time[datetime]');tx=b.select_one('.tgme_widget_message_text')
     if not tm or not tx:continue
     try:age=(now-datetime.fromisoformat(tm['datetime'].replace('Z','+00:00'))).total_seconds()/60
     except:continue
     if 0<=age<=MAX_AGE:blocks.append(b)
   print(f'Kaynak {src}: yeni mesaj={len(blocks)}');fetched.append((src,blocks))
 sent=0
 for src,blocks in fetched:
  for b in blocks:
   try:sent+=int(process(src,b))
   except Exception as e:print(f'ADAY HATA | {src}: {type(e).__name__}: {e}')
 print(f'=== HIZLI TARAMA BİTTİ | gönderilen={sent} ===')
if __name__=='__main__':main()
