from pathlib import Path

TAG='ozelfirsat09-21'

# ---- telegram_sources.py: normal Telegram pipeline ----
p=Path('telegram_sources.py')
s=p.read_text(encoding='utf-8')
s=s.replace("AMAZON_TAG=os.getenv('AMAZON_ASSOCIATE_TAG','').strip()", "AMAZON_TAG=os.getenv('AMAZON_ASSOCIATE_TAG','ozelfirsat09-21').strip() or 'ozelfirsat09-21'")

if 'def product_meta(' not in s:
    anchor="def coupon_code(text):\n"
    helper=r'''def product_meta(site_name,url,fallback_title=''):
 try:
  # URL must already be normalized. Fetch canonical product metadata directly
  # from the store page; this rescues Telegram messages like "Sepete Ekleniyor...".
  r=requests.get(url,headers=HEAD,timeout=10,allow_redirects=True)
  if r.status_code>=400:return fallback_title,None
  soup=BeautifulSoup(r.text,'html.parser')
  title=''
  for sel,attr in [('meta[property="og:title"]','content'),('meta[name="title"]','content'),('title',None),('#productTitle',None)]:
   el=soup.select_one(sel)
   if el:
    title=(el.get(attr) if attr else el.get_text(' ',strip=True)) or ''
    title=re.sub(r'\s+',' ',title).strip()
    if title:break
  image=None
  for sel,attr in [('meta[property="og:image"]','content'),('#landingImage','data-old-hires'),('#landingImage','src'),('img#imgBlkFront','src'),('meta[name="twitter:image"]','content')]:
   el=soup.select_one(sel)
   if el:
    image=(el.get(attr) or '').strip()
    if image:break
  bad=not fallback_title or bool(re.search(r'(?i)^\s*(?:sepete ekleniyor|fırsat ürünü|ürün|amazon)\s*\.*$',fallback_title))
  return (title[:180] if title and bad else fallback_title),image
 except Exception:
  return fallback_title,None

def force_affiliate(site_name,url):
 if site_name!='Amazon' or not url:return url
 try:
  p=urlparse(url); q=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if k.lower()!='tag']; q.append(('tag',AMAZON_TAG))
  return urlunparse((p.scheme or 'https',p.netloc,p.path,p.params,urlencode(q,doseq=True),p.fragment))
 except Exception:
  return url+('&' if '?' in url else '?')+'tag='+AMAZON_TAG

'''
    s=s.replace(anchor,helper+anchor,1)

old="def send(s,u,t,c,p,source,post_id,signal,coupon=None):\n if not valid(s,u):print(f'ATLANDI | {source}:{post_id} | geçersiz link');return False"
new="def send(s,u,t,c,p,source,post_id,signal,coupon=None):\n u=force_affiliate(s,u)\n if not valid(s,u):print(f'ATLANDI | {source}:{post_id} | geçersiz link');return False\n t,image=product_meta(s,u,t)"
s=s.replace(old,new)

old_payload="payload={'chat_id':CHAT,'text':'\\n'.join(lines),'disable_web_page_preview':False,'reply_markup':{'inline_keyboard':[[{'text':'🛒 FIRSATA GİT','url':u}]]}}\n requests.post('https://api.telegram.org/bot'+TOKEN+'/sendMessage',json=payload,timeout=15).raise_for_status()"
new_payload="""kb={'inline_keyboard':[[{'text':'🛒 FIRSATA GİT','url':u}]]}\n rr=None\n if image:\n  try:\n   im=requests.get(image,headers=HEAD,timeout=10,allow_redirects=True)\n   ct=(im.headers.get('content-type') or 'image/jpeg').split(';')[0]\n   if im.ok and len(im.content)>3000:\n    rr=requests.post('https://api.telegram.org/bot'+TOKEN+'/sendPhoto',data={'chat_id':CHAT,'caption':'\\n'.join(lines)[:1024],'reply_markup':__import__('json').dumps(kb,ensure_ascii=False)},files={'photo':('product.jpg',im.content,ct)},timeout=20)\n  except Exception:rr=None\n if not rr or not rr.ok:\n  payload={'chat_id':CHAT,'text':'\\n'.join(lines),'disable_web_page_preview':True,'link_preview_options':{'is_disabled':True},'reply_markup':kb}\n  rr=requests.post('https://api.telegram.org/bot'+TOKEN+'/sendMessage',json=payload,timeout=15)\n rr.raise_for_status()"""
s=s.replace(old_payload,new_payload)
compile(s,str(p),'exec')
p.write_text(s,encoding='utf-8')

# ---- run_trusted_fast_lane.py: fast lane ----
p=Path('run_trusted_fast_lane.py')
s=p.read_text(encoding='utf-8')
if 'AMAZON_TAG=' not in s:
    s=s.replace("HEAD=dict(ts.HEAD);HEAD.update({'Cache-Control':'no-cache, no-store, max-age=0','Pragma':'no-cache'})", "HEAD=dict(ts.HEAD);HEAD.update({'Cache-Control':'no-cache, no-store, max-age=0','Pragma':'no-cache'})\nAMAZON_TAG=os.getenv('AMAZON_ASSOCIATE_TAG','ozelfirsat09-21').strip() or 'ozelfirsat09-21'")
if 'def affiliate_url(' not in s:
    anchor='def fmt(x):'
    helper=r'''def affiliate_url(site,url):
 if site!='Amazon' or not url:return url
 from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
 try:
  u=urlsplit(url);q=[(k,v) for k,v in parse_qsl(u.query,keep_blank_values=True) if k.lower()!='tag'];q.append(('tag',AMAZON_TAG))
  return urlunsplit((u.scheme or 'https',u.netloc,u.path,urlencode(q,doseq=True),u.fragment))
 except Exception:return url+('&' if '?' in url else '?')+'tag='+AMAZON_TAG

def page_meta(url,title):
 try:
  r=requests.get(url,headers=HEAD,timeout=10,allow_redirects=True);soup=BeautifulSoup(r.text,'html.parser')
  pt=soup.select_one('#productTitle') or soup.select_one('meta[property="og:title"]') or soup.select_one('title')
  if pt:
   cand=(pt.get('content') or pt.get_text(' ',strip=True) or '').strip()
   if cand and re.search(r'(?i)sepete ekleniyor|fırsat ürünü',title or ''):title=re.sub(r'\s+',' ',cand)[:170]
  img=soup.select_one('meta[property="og:image"]')
  image=(img.get('content') if img else None)
  if not image:
   img=soup.select_one('#landingImage');image=(img.get('data-old-hires') or img.get('src')) if img else None
  return title,image
 except Exception:return title,None

'''
    s=s.replace(anchor,helper+anchor,1)
s=s.replace("def send(site,url,title,current,ref,campaign,image,row):\n    disc=(ref-current)/ref*100", "def send(site,url,title,current,ref,campaign,image,row):\n    url=affiliate_url(site,url)\n    title,page_image=page_meta(url,title)\n    image=image or page_image\n    disc=(ref-current)/ref*100")
# User explicitly wants fallback text if no photo: keep sendMessage fallback, but tag must survive there too.
compile(s,str(p),'exec')
p.write_text(s,encoding='utf-8')

print('ROOT V2 OK | Amazon tag zorunlu | ürün adı mağazadan | foto mağazadan aranır | bulunamazsa metin paylaşımı devam eder')
