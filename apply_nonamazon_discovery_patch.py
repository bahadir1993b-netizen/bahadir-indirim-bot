from pathlib import Path
import re

p = Path('marketplace_nonamazon_scanner.py')
s = p.read_text(encoding='utf-8')

# Arama sonuçlarında iki fiyat şartını kaldır: ürün sayfası gerçek indirimi
# doğrulayacak. Böylece HB/Trendyol'da tek görünen fiyatlı kartlar da aday olur.
s = s.replace("if len(vals)<2:continue\n  current=min(vals);display_old=max(vals)\n  if display_old<=current or display_old/current>4:continue", "if not vals:continue\n  current=min(vals);display_old=max(vals) if len(vals)>1 else None\n  if display_old is not None and (display_old<=current or display_old/current>4):continue")

start = s.index('def verify(')
end = s.index('\ndef save_post(', start)
new_verify = r'''def verify(page,site,u,expected):
 try:
  page.goto(u,wait_until='domcontentloaded',timeout=10000);page.wait_for_timeout(700);soup=BeautifulSoup(page.content(),'html.parser');page_text=soup.get_text(' ',strip=True)
  vals=[]
  for el in soup.select('meta[itemprop="price"],meta[property="product:price:amount"],[itemprop="price"],[data-price]'):
   v=money(el.get('content') or el.get('value') or el.get('data-price') or el.get_text(' ',strip=True));
   if v and v>0:vals.append(v)
  for el in soup.select('script[type="application/ld+json"]'):
   for m in re.finditer(r'"price"\s*:\s*"?([0-9]+(?:[.,][0-9]+)?)',el.get_text(' ',strip=True)):
    v=money(m.group(1));
    if v and v>0:vals.append(v)
  if not vals:return None
  current=min(vals,key=lambda v:abs(v-expected))
  if abs(current-expected)/max(expected,1)>.05:
   print(f'NON-AMAZON FİYAT RED | {site} | arama={expected:.2f} | canlı={current:.2f}');return None

  # Önce daha önce gerçekten gördüğümüz fiyatı kullan.
  old=history(site,u,current)

  # İlk gözlemde de ürün sayfasının KENDİ açık eski/liste fiyatı varsa
  # fırsatı yakala. Arama kartındaki ikinci sayı burada asla kullanılmaz.
  page_old=[]
  old_selectors=[
   'del','s','strike','[data-old-price]','[data-previous-price]',
   '[data-test-id*="old" i]','[data-test-id*="prev" i]',
   '[class*="oldPrice" i]','[class*="old-price" i]',
   '[class*="previousPrice" i]','[class*="previous-price" i]',
   '[class*="prevPrice" i]','[class*="prev-price" i]'
  ]
  for el in soup.select(','.join(old_selectors)):
   txt=el.get('data-old-price') or el.get('data-previous-price') or el.get_text(' ',strip=True)
   for m in MONEY.finditer(txt or ''):
    v=money(m.group())
    if v and v>current and v/current<=4:page_old.append(v)
  label_patterns=[
   r'(?:eski fiyat|önceki fiyat|liste fiyatı|piyasa fiyatı)\s*[:\-]?\s*('+MONEY.pattern+r')',
   r'(?:yerine|önceden)\s*[:\-]?\s*('+MONEY.pattern+r')'
  ]
  for pat in label_patterns:
   for m in re.finditer(pat,page_text,re.I):
    v=money(m.group(1))
    if v and v>current and v/current<=4:page_old.append(v)
  if page_old:
   explicit_old=min(page_old)
   if not old or explicit_old<old:old=explicit_old

  if not old or old<=current:
   record(site,u,current);print(f'NON-AMAZON İLK KAYIT | {site} | {current:.2f} TL | doğrulanmış eski fiyat yok');return None
  d=(old-current)/old*100;record(site,u,current)
  if d<MIN_DISCOUNT:return None
  te=soup.select_one('meta[property="og:title"]') or soup.select_one('meta[name="twitter:title"]');ie=soup.select_one('meta[property="og:image"]') or soup.select_one('meta[name="twitter:image"]')
  title=(te.get('content','').strip() if te else (soup.title.get_text(' ',strip=True) if soup.title else site))[:220]
  return title,current,old,d,ie.get('content') if ie else None
 except Exception as e:print(f'NON-AMAZON VERIFY HATA | {site} | {type(e).__name__}: {e}');return None
'''
s = s[:start] + new_verify + s[end:]
p.write_text(s,encoding='utf-8')
compile(s,str(p),'exec')
print('NON-AMAZON DISCOVERY PATCH OK | tek fiyatlı kartlar aday | ürün sayfası eski fiyatı veya gerçek geçmiş kullanılır')
