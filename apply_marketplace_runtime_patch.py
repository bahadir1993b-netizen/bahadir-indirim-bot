from pathlib import Path
import re
P=Path('marketplace_scanner.py'); s=P.read_text(encoding='utf-8')
new_verify=r'''def verify(page,site,u,fallback_title,expected_current,expected_previous):
 try:
  page.goto(u,wait_until='domcontentloaded',timeout=12000); page.wait_for_timeout(700)
  soup=BeautifulSoup(page.content(),'html.parser')
  title_el=soup.select_one('meta[property="og:title"]') or soup.select_one('meta[name="twitter:title"]')
  title=title_el.get('content','').strip() if title_el else (soup.title.get_text(' ',strip=True) if soup.title else fallback_title)
  image_el=soup.select_one('meta[property="og:image"]') or soup.select_one('meta[name="twitter:image"]'); image=image_el.get('content','').strip() if image_el else None
  if re.search(r'\b(kitap|kitapları|roman|dergi|magazin|e-kitap|ebook|yayınevi|yayıncılık)\b',(title+' '+fallback_title),re.I): return None
  def toks(x):
   return {z for z in re.findall(r'[a-zçğıöşü0-9]{3,}',(x or '').lower()) if z not in {'amazon','com','tr','ürün','ürünü','fırsat','indirim','adet','paket','tl'}}
  ft=toks(fallback_title); pt=toks(title)
  if ft and pt and len(ft&pt)/max(1,len(ft))<0.45:
   print(f'VERIFY ÜRÜN UYUŞMAZ | {site} | kaynak={fallback_title[:80]} | sayfa={title[:80]} | ATLANDI'); return None
  current_vals=[]
  if site=='Amazon':
   for sel in ['#corePriceDisplay_desktop_feature_div .a-price:not(.a-text-price) .a-offscreen','#corePrice_feature_div .a-price:not(.a-text-price) .a-offscreen','#apex_desktop .a-price:not(.a-text-price) .a-offscreen','#priceblock_ourprice','#priceblock_dealprice','#price_inside_buybox','#newBuyBoxPrice','[data-a-color="price"] .a-offscreen','span.a-price:not(.a-text-price) span.a-offscreen']:
    for e in soup.select(sel):
     x=money(e.get_text(' ',strip=True) or e.get('content') or '');
     if x: current_vals.append(x)
  else:
   for sel in ['meta[property="product:price:amount"]','meta[itemprop="price"]','[itemprop="price"]','[data-price]']:
    for e in soup.select(sel):
     x=money(e.get('content') or e.get('value') or e.get('data-price') or e.get_text(' ',strip=True));
     if x: current_vals.append(x)
   if not current_vals: current_vals=prices(soup.get_text(' ',strip=True))[:50]
  if not current_vals:return None
  current=min(current_vals,key=lambda x:abs(x-expected_current)) if expected_current else current_vals[0]
  if expected_current and abs(current-expected_current)/max(expected_current,1)>0.08:
   print(f'PRICE FRESHNESS MISMATCH | {site} | beklenen={expected_current:.2f} | sayfa={current:.2f} | ATLANDI'); return None
  previous=None
  try:
   rows=sb('GET','price_history',params={'select':'price,recorded_at','product_url':f'eq.{u}','site':f'eq.{site}','order':'recorded_at.desc','limit':'60'})
   hist=sorted([float(r['price']) for r in rows if r.get('price') is not None and float(r['price'])>current])
   if hist: previous=hist[0]
  except Exception as e: print('HISTORY HATA',site,e)
  if not previous:
   old=[]
   for sel in ['del','s','.old-price','.list-price','.price-old','[class*="oldPrice"]','[class*="old-price"]','[class*="listPrice"]','[data-a-strike="true"]','[aria-label*="eski"]','[aria-label*="liste"]','.a-text-price .a-offscreen','#basisPrice_feature_div .a-offscreen','#corePriceDisplay_desktop_feature_div .a-text-price .a-offscreen','#corePrice_feature_div .a-text-price .a-offscreen']:
    for e in soup.select(sel):
     x=money(e.get_text(' ',strip=True) or e.get('content') or '')
     if x and x>current: old.append(x)
   txt=soup.get_text(' ',strip=True)
   for pat in [r'(?:Liste Fiyatı|Eski fiyat|Önceki fiyat|Normal fiyat|Tavsiye edilen satış fiyatı)\s*[:]?\s*([\d.,]+)\s*(?:TL|₺)',r'(?:was|list price|previous price|regular price)\s*[:]?\s*([\d.,]+)\s*(?:TL|₺)']:
    for m in re.finditer(pat,txt,re.I):
     x=money(m.group(1));
     if x and x>current: old.append(x)
   if old: previous=min(old)
  if not previous or previous<=current:
   try: sb('POST','price_history',json={'price':current,'product_url':u,'site':site,'recorded_at':datetime.now(timezone.utc).isoformat()})
   except: pass
   print(f'VERIFY ESKİ FİYAT YOK | {site} | mevcut={current:.2f} | ilk gözlem/ATLANDI'); return None
  ratio=previous/current
  if ratio>4.0:
   print(f'VERIFY ESKİ FİYAT ŞÜPHELİ | {site} | mevcut={current:.2f} | önceki={previous:.2f} | oran={ratio:.1f}x | ATLANDI'); return None
  if site=='Amazon':
   try:
    model=re.findall(r'\b[A-Z]{1,6}\d{2,}[A-Z0-9-]*\b',title.upper())
    queries=[]
    if model: queries.append(' '.join(model[:2]))
    words=[w for w in re.findall(r'[A-Za-zÇĞİÖŞÜçğıöşü0-9]+',title) if len(w)>2]
    queries += [' '.join(words[:24]),' '.join(words[:14]),' '.join(words[:8])]
    ak_min=None
    for q in queries:
     if not q: continue
     r=requests.get('https://www.akakce.com/arama/?q='+requests.utils.quote(q),headers=HEAD,timeout=6)
     if not r.ok: continue
     ak=BeautifulSoup(r.text,'html.parser')
     for card in ak.select('li.pt_v8, .p-item, .m_i, .product'):
      ct=card.get_text(' ',strip=True); score=len(toks(title)&toks(ct))/max(1,len(toks(title)))
      if model and not any(m in ct.upper() for m in model): continue
      if score<0.20: continue
      for el in card.select('.price,.fiyat,[class*="price"]'):
       x=money(el.get_text(' ',strip=True))
       if x and x>1: ak_min=x if ak_min is None else min(ak_min,x)
    if ak_min:
     market_disc=(ak_min-current)/ak_min*100 if ak_min>current else 0
     if market_disc < MIN_DISCOUNT:
      print(f'AKAKCE PIYASA AVANTAJI YETERSİZ | Amazon={current:.2f} | Akakçe={ak_min:.2f} | %{market_disc:.1f} | ATLANDI'); return None
     p_market=ak_min
     if p_market>current:
      previous=p_market
      print(f'AKAKCE REFERANS | mevcut={current:.2f} | piyasa={p_market:.2f} | gerçek indirim=%{market_disc:.1f}')
  disc=(previous-current)/previous*100
  if disc<MIN_DISCOUNT:return None
  try: sb('POST','price_history',json={'price':current,'product_url':u,'site':site,'recorded_at':datetime.now(timezone.utc).isoformat()})
  except: pass
  return clean_title(title),current,previous,disc,image
 except Exception as e: print('VERIFY HATA',site,u,e); return None
'''
s2,n=re.subn(r'def verify\(page,site,u,fallback_title,expected_current,expected_previous\):.*?(?=\ndef send\()',lambda m:new_verify.rstrip()+'\n',s,count=1,flags=re.S)
if n!=1: raise SystemExit('verify fonksiyonu bulunamadı')
P.write_text(s2,encoding='utf-8'); print('Marketplace doğrulama güncellendi: canlı fiyat + sıkı Akakçe piyasa avantajı + gerçek geçmiş')
