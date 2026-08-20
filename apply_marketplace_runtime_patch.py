from pathlib import Path
import re

P=Path('marketplace_scanner.py')
s=P.read_text(encoding='utf-8')

new_verify=r'''def verify(page,site,u,fallback_title,expected_current,expected_previous):
 try:
  page.goto(u,wait_until='domcontentloaded',timeout=12000)
  page.wait_for_timeout(900)
  soup=BeautifulSoup(page.content(),'html.parser')
  title=(soup.select_one('meta[property="og:title"]') or soup.select_one('meta[name="twitter:title"]'))
  title=title.get('content','').strip() if title else ''
  if not title:title=soup.title.get_text(' ',strip=True) if soup.title else fallback_title
  image=(soup.select_one('meta[property="og:image"]') or soup.select_one('meta[name="twitter:image"]'))
  image=image.get('content','').strip() if image else None

  # Güncel fiyat: Amazon'da yalnızca gerçek satış/Buy Box alanları.
  current_vals=[]
  if site=='Amazon':
   for sel in ['#corePriceDisplay_desktop_feature_div .a-price:not(.a-text-price) .a-offscreen','#corePrice_feature_div .a-price:not(.a-text-price) .a-offscreen','#apex_desktop .a-price:not(.a-text-price) .a-offscreen','#priceblock_ourprice','#priceblock_dealprice','#price_inside_buybox','#newBuyBoxPrice','[data-a-color="price"] .a-offscreen','span.a-price:not(.a-text-price) span.a-offscreen']:
    for e in soup.select(sel):
     x=money(e.get_text(' ',strip=True) or e.get('content') or '')
     if x:current_vals.append(x)
  else:
   for sel in ['meta[property="product:price:amount"]','meta[itemprop="price"]','[itemprop="price"]','[data-price]']:
    for e in soup.select(sel):
     x=money(e.get('content') or e.get('value') or e.get('data-price') or e.get_text(' ',strip=True))
     if x:current_vals.append(x)
   if not current_vals:current_vals=prices(soup.get_text(' ',strip=True))[:50]
  if not current_vals:return None
  current=min(current_vals,key=lambda x:abs(x-expected_current)) if expected_current else current_vals[0]
  if expected_current and abs(current-expected_current)/max(expected_current,1)>0.35:
   print(f'PRICE MISMATCH | {site} | beklenen={expected_current:.2f} | sayfa={current:.2f} | ATLANDI'); return None

  # Eski fiyat yalnızca açık old/list/strike alanlarından alınır.
  old=[]
  sels=['del','s','.old-price','.list-price','.price-old','[class*="oldPrice"]','[class*="old-price"]','[class*="listPrice"]','[data-a-strike="true"]','[aria-label*="eski"]','[aria-label*="liste"]','.a-text-price .a-offscreen','#basisPrice_feature_div .a-offscreen','#corePriceDisplay_desktop_feature_div .a-text-price .a-offscreen','#corePrice_feature_div .a-text-price .a-offscreen']
  for sel in sels:
   for e in soup.select(sel):
    x=money(e.get_text(' ',strip=True) or e.get('content') or '')
    if x and x>current:old.append(x)
  txt=soup.get_text(' ',strip=True)
  for pat in [r'(?:Liste Fiyatı|Eski fiyat|Önceki fiyat|Normal fiyat|Tavsiye edilen satış fiyatı)\s*[:]?\s*([\d.,]+)\s*(?:TL|₺)',r'(?:was|list price|previous price|regular price)\s*[:]?\s*([\d.,]+)\s*(?:TL|₺)']:
   for m in re.finditer(pat,txt,re.I):
    x=money(m.group(1))
    if x and x>current:old.append(x)
  previous=max(old) if old else None
  if not previous or previous<=current:
   print(f'VERIFY ESKİ FİYAT YOK | {site} | mevcut={current:.2f} | ATLANDI'); return None
  ratio=previous/current
  if ratio>4.0:
   print(f'VERIFY ESKİ FİYAT ŞÜPHELİ | {site} | mevcut={current:.2f} | önceki={previous:.2f} | oran={ratio:.1f}x | ATLANDI'); return None

  # Amazon fiyatı piyasadaki belirgin daha ucuz seçeneklerden yüksekse gerçek fırsat sayma.
  if site=='Amazon':
   try:
    q=' '.join(re.sub(r'Amazon\.com\.tr.*$','',title,flags=re.I).split())[:180]
    r=requests.get('https://www.akakce.com/arama/?q='+requests.utils.quote(q),headers=HEAD,timeout=6)
    if r.ok:
     ak=BeautifulSoup(r.text,'html.parser'); vals=[]
     for e in ak.select('.pt_v8,.price,.fiyat,[class*="price"]'):
      x=money(e.get_text(' ',strip=True))
      if x and x>1:vals.append(x)
     if vals and min(vals)<current*0.85:
      print(f'AKAKCE DAHA UCUZ | Amazon={current:.2f} | Akakçe={min(vals):.2f} | ATLANDI'); return None
   except Exception as e: print('AKAKCE KONTROL HATA',e)

  disc=(previous-current)/previous*100
  if disc<MIN_DISCOUNT:return None
  return clean_title(title),current,previous,disc,image
 except Exception as e:
  print('VERIFY HATA',site,u,e); return None
'''

s2,n=re.subn(r'def verify\(page,site,u,fallback_title,expected_current,expected_previous\):.*?(?=\ndef send\()',lambda m:new_verify.rstrip()+'\n',s,count=1,flags=re.S)
if n!=1: raise SystemExit('verify fonksiyonu bulunamadı')
P.write_text(s2,encoding='utf-8')
print('Marketplace doğrulama: eski fiyat sadece doğrulanmış old/list alanından + Amazon Akakçe kontrolü')
