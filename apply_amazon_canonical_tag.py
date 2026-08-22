from pathlib import Path

p=Path('telegram_sources.py')
s=p.read_text(encoding='utf-8')
s=s.replace("AMAZON_TAG=os.getenv('AMAZON_ASSOCIATE_TAG','').strip()", "AMAZON_TAG=os.getenv('AMAZON_ASSOCIATE_TAG','ozelfirsat09-21').strip() or 'ozelfirsat09-21'")
old="""def normalize(s,u):
 if not u or s not in MARKET.values():return None
 p=urlparse(clean(u)); q=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if k.lower() not in TRACKING]
 if s=='Amazon' and AMAZON_TAG:q.append(('tag',AMAZON_TAG))
 return urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q,doseq=True),''))
"""
new="""def amazon_asin(u):
 m=re.search(r'/(?:dp|gp/product)/([A-Z0-9]{8,12})(?:[/?]|$)',urlparse(clean(u)).path,re.I)
 return m.group(1).upper() if m else None

def normalize(s,u):
 if not u or s not in MARKET.values():return None
 p=urlparse(clean(u))
 if s=='Amazon':
  asin=amazon_asin(u)
  if asin:return f'https://www.amazon.com.tr/dp/{asin}?tag={AMAZON_TAG}'
 q=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if k.lower() not in TRACKING]
 return urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q,doseq=True),''))
"""
if old in s:s=s.replace(old,new)
elif 'def amazon_asin(' not in s:raise SystemExit('normalize bloğu bulunamadı')
# save/send girişinde de normalize et ki eski çağrı yolları tagsiz çıkamasın.
s=s.replace("def save(s,u,t,c,p):\n now=", "def save(s,u,t,c,p):\n u=normalize(s,u) or u\n now=")
s=s.replace("def send(s,u,t,c,p,source,post_id,signal,coupon=None):\n if not valid", "def send(s,u,t,c,p,source,post_id,signal,coupon=None):\n u=normalize(s,u) or u\n if not valid")
compile(s,str(p),'exec')
p.write_text(s,encoding='utf-8')
print('AMAZON CANONICAL TAG OK | tag=ozelfirsat09-21 | ASIN bazlı URL')
