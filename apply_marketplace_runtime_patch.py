from pathlib import Path
import re

P=Path('marketplace_scanner.py')
s=P.read_text(encoding='utf-8')

new_verify=r'''def verify(page,site,u,fallback_title,expected_current,expected_previous):
 try:
  page.goto(u,wait_until='domcontentloaded',timeout=12000)
  page.wait_for_timeout(900)
  html=page.content(); soup=BeautifulSoup(html,'html.parser')
  title=(soup.select_one('meta[property="og:title"]') or soup.select_one('meta[name="twitter:title"]'))
  title=title.get('content','').strip() if title else ''
  if not title:title=(soup.title.get_text(' ',strip=True) if soup.title else fallback_title)
  image=(soup.select_one('meta[property="og:image"]') or soup.select_one('meta[name="twitter:image"]'))
  image=image.get('content','').strip() if image else None

  # Amazon: search-card fiyatını ASLA gerçek ürün fiyatı kabul etme.
  # Önce Buy Box / priceToPay / a-price alanlarından gerçek satış fiyatını al.
  if site=='Amazon':
   amazon_vals=[]
   selectors=[
    '#corePriceDisplay_desktop_feature_div .a-price:not(.a-text-price) .a-offscreen',
    '#corePrice_feature_div .a-price:not(.a-text-price) .a-offscreen',
    '#apex_desktop .a-price:not(.a-text-price) .a-offscreen',
    '#priceblock_ourprice', '#priceblock_dealprice',
    '#price_inside_buybox', '#newBuyBoxPrice',
    '[data-a-color="price"] .a-offscreen',
    'span.a-price:not(.a-text-price) span.a-offscreen'
   ]
   for sel in selectors:
    for e in soup.select(sel):
     x=money(e.get_text(' ',strip=True) or e.get('content') or '')
     if x and x not in amazon_vals: amazon_vals.append(x)
   # Son çare: canlı görünen fiyat alanlarını kullan, ama kupon/taksit bağlamını ele.
   if not amazon_vals:
    for e in soup.select('.a-price:not(.a-text-price) .a-offscreen'):
     txt=e.get_text(' ',strip=True)
     x=money(txt)
     if x and not BAD_PRICE_CONTEXT.search(txt): amazon_vals.append(x)
   if not amazon_vals:return None
   current=amazon_vals[0]
  else:
   current_vals=[]
   old=[]
   for sel in ['meta[property="product:price:amount"]','meta[itemprop="price"]','[itemprop="price"]','[data-price]']:
    for e in soup.select(sel):
     x=money(e.get('content') or e.get('value') or e.get('data-price') or e.get_text(' ',strip=True))
     if x:current_vals.append(x)
   for sel in ['del','s','.old-price','.list-price','.price-old','[class*="oldPrice"]','[class*="old-price"]','[class*="listPrice"]','[class*="discountedPrice"]']:
    for e in soup.select(sel):
     x=money(e.get_text(' ',strip=True))
     if x:old.append(x)
   if not current_vals:current_vals=prices(soup.get_text(' ',strip=True))[:50]
   if not current_vals:return None
   plausible=[x for x in current_vals if x>=max(1,expected_current*0.50)] or current_vals
   current=min(plausible,key=lambda x:abs(x-expected_current))

  # Search sonucu ile ürün sayfası arasında büyük fark varsa ilanı reddet.
  # Bu özellikle Amazon'daki 90 TL kupon/taksit değerinin fiyat sanılmasını engeller.
  if expected_current and abs(current-expected_current)/max(expected_current,1)>0.35:
   print(f'PRICE MISMATCH | {site} | beklenen={expected_current:.2f} | ürün_sayfası={current:.2f} | ATLANDI')
   return None

  old=[]
  for sel in ['del','s','.old-price','.list-price','.price-old','[class*="oldPrice"]','[class*="old-price"]','[class*="listPrice"]','[class*="discountedPrice"]']:
   for e in soup.select(sel):
    x=money(e.get_text(' ',strip=True))
    if x:old.append(x)
  previous=max(old or [],default=None)
  if not previous:
   vals=prices(soup.get_text(' ',strip=True))
   previous=max([x for x in vals if x>current],default=None)
  if not previous or previous<=current:previous=expected_previous
  if not previous or previous<=current:return None
  disc=(previous-current)/previous*100
  if disc<MIN_DISCOUNT:return None
  # %95+ indirimde ürün sayfası gerçek fiyatı doğrulamıyorsa yayınlama.
  if disc>=95 and expected_current and current<expected_current*0.50:return None
  return clean_title(title),current,previous,disc,image
 except Exception as e:
  print('VERIFY HATA',site,u,e); return None
'''

s2,n=re.subn(r'def verify\(page,site,u,fallback_title,expected_current,expected_previous\):.*?(?=\ndef send\()',lambda m:new_verify.rstrip()+'\n',s,count=1,flags=re.S)
if n!=1: raise SystemExit('verify fonksiyonu bulunamadı')
P.write_text(s2,encoding='utf-8')
print('Amazon gerçek ürün fiyatı doğrulama patch uygulandı')
