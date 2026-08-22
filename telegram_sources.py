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
# SUPABASE_URL bazen proje kökü, bazen de /rest/v1 ile kaydedilmiş oluyor.
# Tek bir normalize edilmiş kök kullan; aksi halde /rest/v1/rest/v1/... 404 oluşur.
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
 p=urlparse(u); h=p.netloc.lower().replace('www.',''); path=p.path
 if s=='Amazon':
  m=re.search(r'/(?:dp|gp/product)/([A-Z0-9]{8,})',path,re.I)
  return f'https://www.amazon.com.tr/dp/{m.group(1).upper()}' if m else None
 if s=='Hepsiburada':return urlunparse(('https','www.hepsiburada.com',path,'','',''))
 if s=='Trendyol':return urlunparse(('https','www.trendyol.com',path,'','',''))
 return None

# Remaining functions follow below unchanged in repository history.