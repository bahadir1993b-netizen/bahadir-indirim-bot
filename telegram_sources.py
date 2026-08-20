import os,re,html as htmlmod,requests
from datetime import datetime,timezone,timedelta
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

TOKEN=os.environ['TELEGRAM_BOT_TOKEN']; SB=os.environ['SUPABASE_URL'].rstrip('/'); KEY=os.environ['SUPABASE_SERVICE_KEY']
CHAT='-1004424116637'; MAX_AGE=180; MIN_DISCOUNT=10.0; COOLDOWN=12
HEAD={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36','Accept-Language':'tr-TR,tr;q=0.9,en;q=0.8'}
SOURCES={'OnuAl':'onual_firsat','EnesOzen':'enesozen'}
MARKET={'amazon.com.tr':'Amazon','hepsiburada.com':'Hepsiburada','trendyol.com':'Trendyol'}
SHORT={'app.hb.biz':'Hepsiburada','hps.im':'Hepsiburada','ty.gl':'Trendyol','tyml.gl':'Trendyol','link.amazon':'Amazon','amzn.to':'Amazon','amzn.eu':'Amazon','publicis.link':None,'onu.al':None,'sl.n11.com':None}

def sb(method,path,**kw):
 h={'apikey':KEY,'Authorization':f'Bearer {KEY}','Content-Type':'application/json','Accept':'application/json'}
 if method=='POST':h['Prefer']='return=representation'
 r=requests.request(method,f'{SB}/rest/v1/{path}',headers=h,timeout=15,**kw);r.raise_for_status();return r.json() if r.text else []

def money(s):
 s=re.sub(r'[^0-9,.]','',str(s).replace('TL','').replace('₺','').replace(' ',''))
 if not s:return None
 if ',' in s and '.' in s:s=s.replace('.','').replace(',','.') if s.rfind(',')>s.rfind('.') else s.replace(',','')
 elif ',' in s:
  a,b=s.rsplit(',',1);s=a.replace('.','')+'.'+b if len(b)<=2 else s.replace(',','')
 elif '.' in s:
  a,b=s.rsplit('.',1);s=s.replace('.','') if len(b)>2 else s
 try:
  x=float(s);return x if 0<x<10000000 else None
 except:return None

MONEY_RE=re.compile(r'(?<![A-ZÇĞİÖŞÜ])(?:\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)(?![A-ZÇĞİÖŞÜ])',re.I)
def prices(text):return [money(m.group(0)) for m in MONEY_RE.finditer(text or '') if money(m.group(0)) is not None]

def extract_price_pair(text):
 m=re.search(r'(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)\s+yerine\s+(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:TL|₺)',text,re.I)
 if m:return money(m.group(2)),money(m.group(1))
 m=re.search(r'(?:yeni|önceki|onceki)\s*[:=]?\s*(\d[\d.,]*)\s*(?:TL|₺).*?(?:şuan|şu an|simdi|şimdi)\s*[:=]?\s*(\d[\d.,]*)\s*(?:TL|₺)',text,re.I|re.S)
 if m:return money(m.group(2)),money(m.group(1))
 p=prices(text);return (p[0],None) if p else (None,None)

def site_url(u):
 host=urlparse(u).netloc.lower().replace('www.','')
 if host in MARKET:return MARKET[host]
 for h,s in SHORT.items():
  if host==h or host.endswith('.'+h):return s
 return None

def valid(site,u):
 p=urlparse(u);host=p.netloc.lower().replace('www.','');path=p.path
 if site=='Amazon':return host.endswith('amazon.com.tr') and bool(re.search(r'/(?:dp|gp/product)/[A-Z0-9]{8,}',path,re.I))
 if site=='Hepsiburada':return host.endswith('hepsiburada.com') and bool(re.search(r'-p-[A-Za-z0-9]+(?:[/?#&]|$)',path,re.I))
 if site=='Trendyol':return host.endswith('trendyol.com') and bool(re.search(r'-p-\d+(?:[/?#&]|$)',path,re.I))
 return False

def resolve(page,u):
 original=u
 try:
  r=requests.get(u,headers=HEAD,timeout=10,allow_redirects=True);u=r.url
 except:pass
 s=site_url(u)
 if s and valid(s,u):
  print(f'Link çözüldü: {s} | {clean(u)}');return u
 host=urlparse(original).netloc.lower().replace('www.','')
 if host in SHORT or host.endswith('.onu.al') or host.endswith('.publicis.link'):
  try:
   page.goto(original,wait_until='domcontentloaded',timeout=15000);page.wait_for_timeout(1200);final=page.url
   s=site_url(final)
   if s and valid(s,final):
    print(f'Link çözüldü: {s} | {clean(final)}');return final
  except:pass
 return u

def clean(u):return htmlmod.unescape(u).replace('\\/','/').split('#',1)[0].rstrip('/')

def seen(key):return bool(sb('GET','price_history',params={'select':'recorded_at','product_url':f'eq.telegram://{key}','limit':'1'}))
def remember(key):sb('POST','price_history',json={'price':0,'product_url':f'telegram://{key}','site':'telegram','recorded_at':datetime.now(timezone.utc).isoformat()})

def save(site,url,title,current,previous):
 now=datetime.now(timezone.utc).isoformat();rows=sb('GET','products',params={'select':'*','product_url':f'eq.{url}','limit':'1'})
 payload={'product_name':title,'current_price':current,'previous_price':previous,'product_url':url,'site':site,'updated_at':now}
 if rows:sb('PATCH',f'products?id=eq.{rows[0]["id"]}',json=payload);return rows[0]
 return (sb('POST','products',json=payload) or [payload])[0]

def send(site,url,title,current,previous,source,post_id,signal):
 key=f'{source}:{post_id}'
 if seen(key):return False
 disc=None
 if previous and previous>current:
  disc=(previous-current)/previous*100
  if disc<MIN_DISCOUNT:remember(key);return False
 strong=bool(previous and previous>current) or bool(re.search(r'en düşük|son \d+ gün|son \d+ ay|son \d+ yıl|dip fiyat|ortalama fiyatın|düştü|sepette|kupon|kod(?:u)?|2 al 1|3 al 2|4 al 3|kampanya|indirim|aktif',signal,re.I))
 if not strong:remember(key);return False
 row=save(site,url,title,current,previous);last=row.get('last_posted_at') if isinstance(row,dict) else None
 if last:
  try:
   if datetime.now(timezone.utc)-datetime.fromisoformat(last.replace('Z','+00:00'))<timedelta(hours=COOLDOWN):remember(key);return False
  except:pass
 lines=[f'🔥 %{disc:.0f} İNDİRİM' if disc is not None else '🔥 SICAK FIRSAT','',title,'',f'💰 {current:,.2f} TL']
 if disc is not None:lines.append(f'🏷️ Önce: {previous:,.2f} TL')
 lines += [f'🛍️ {site}',f'🔗 {url}']
 if signal:lines += ['',f'📌 {re.sub(r"\s+"," ",signal)[:260]}']
 lines += ['',f'Kaynak: {source}']
 requests.post('https://api.telegram.org/bot'+TOKEN+'/sendMessage',json={'chat_id':CHAT,'text':'\n'.join(lines)},timeout=15).raise_for_status()
 sb('PATCH',f'products?id=eq.{row["id"]}',json={'last_posted_at':datetime.now(timezone.utc).isoformat()});remember(key)
 print(f'GÖNDERİLDİ | {site} | {title[:70]} | {current:.2f} | kaynak={source}');return True

def parse(block,channel,page):
 tm=block.select_one('time[datetime]')
 if not tm:return None
 try:dt=datetime.fromisoformat(tm['datetime'].replace('Z','+00:00'))
 except:return None
 age=datetime.now(timezone.utc)-dt
 if age<timedelta(0) or age>timedelta(minutes=MAX_AGE):return None
 node=block.select_one('.tgme_widget_message_text')
 if not node:return None
 raw=node.get_text('\n',strip=True);links=[]
 # OnuAl'ın "Aç" bağlantısı mesaj metninin dışında; bu yüzden tüm mesaj bloğunu tarıyoruz.
 for a in block.select('a[href]'):
  u=htmlmod.unescape(a.get('href',''))
  if not u.startswith('http'):continue
  host=urlparse(u).netloc.lower().replace('www.','')
  if host not in MARKET and host not in SHORT and not host.endswith('.onu.al') and not host.endswith('.publicis.link'):continue
  r=resolve(page,u);s=site_url(r) or site_url(u)
  if s and valid(s,r):
   r=clean(r)
   if r not in [x[1] for x in links]:links.append((s,r))
 if not links:return None
 site,url=links[0];current,previous=extract_price_pair(raw)
 if current is None:return None
 lines=[x.strip() for x in raw.splitlines() if x.strip()];title='Ürün'
 for line in lines:
  if re.search(r'(TL|₺|yerine|kupon|kod(?:u)?|sepette|indirim|kampanya)',line,re.I) or line.startswith(('http','#')):continue
  title=re.sub(r'^[^A-Za-zÇĞİÖŞÜ0-9]+','',line).strip()
  if len(title)>=4:break
 post_id=block.get('data-post','').split('/')[-1]
 if not post_id.isdigit():return None
 signal=' | '.join(x for x in lines[1:] if re.search(r'en düşük|son \d+|dip|ortalama|düştü|sepette|kupon|kod(?:u)?|kampanya|indirim|aktif|2 al 1|3 al 2|4 al 3',x,re.I))[:500]
 return site,url,title[:300],current,previous,post_id,signal

def main():
 print('=== Telegram fırsat keşfi başladı ===');candidates=[];sent=0
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True,args=['--no-sandbox'])
  try:
   for source,channel in SOURCES.items():
    r=requests.get(f'https://t.me/s/{channel}',headers=HEAD,timeout=20);print(f'Telegram kaynak {source}: HTTP {r.status_code}')
    if r.status_code>=400:continue
    soup=BeautifulSoup(r.text,'html.parser');page=browser.new_page();page.set_default_timeout(15000)
    try:
     for block in soup.select('.tgme_widget_message'):
      item=parse(block,channel,page)
      if item:candidates.append((source,item));print(f'Aday: {item[0]} | {item[2][:80]} | {item[3]:.2f} TL | kaynak={source}')
    finally:page.close()
  finally:browser.close()
 for source,item in candidates:
  try:sent+=1 if send(*item[:5],source,item[5],item[6]) else 0
  except Exception as e:print(f'Ürün işlem hata: {type(e).__name__}: {e}')
 print(f'=== Telegram fırsat keşfi bitti. Aday={len(candidates)} Gönderilen={sent} ===')

if __name__=='__main__':main()
