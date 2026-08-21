from pathlib import Path
import re

P=Path('telegram_fast_sources.py')
s=P.read_text(encoding='utf-8')

start=s.find('def source_image(b):')
end=s.find('\ndef send_photo_or_text(',start)
if start!=-1 and end!=-1:
    new=r'''def source_image(b):
    wrap=b.select_one('.tgme_widget_message_photo_wrap')
    if wrap:
        st=wrap.get('style','')
        m=re.search(r"url\(['\"]?([^'\")]+)",st)
        if m:return clean(m.group(1))
        href=clean(wrap.get('href') or '')
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
            except Exception as e:print(f'KAYNAK FOTO HREF HATA | {type(e).__name__}')
    im=b.select_one('.tgme_widget_message_photo img, .tgme_widget_message_photo_wrap img')
    return clean(im.get('src') or im.get('data-src') or '') if im else None
'''
    s=s[:start]+new+s[end:]

s=s.replace("requests.get(image,headers=HEAD,timeout=8)","requests.get(image,headers={**HEAD,'Referer':'https://t.me/'},timeout=8)")

marker='def process(source,b):\n'
if marker not in s:
    raise RuntimeError('process() bulunamadı')

fn=r'''def campaign_lines(raw):
 raw=raw or ''
 out=[]
 patterns=[
  (r'(?i)\b([A-Z0-9][A-Z0-9_-]{3,})\s+Koduyla\b', lambda m:f'{m.group(1)} Koduyla'),
  (r'(?i)\b([A-Z0-9][A-Z0-9_-]{3,})\s+Kodu(?:\s+ile)?\b', lambda m:f'{m.group(1)} Kodu ile'),
  (r'(?i)\bKupon(?:\s+kodu)?\s*[:=-]\s*([A-Z0-9][A-Z0-9_-]{3,})', lambda m:f'Kupon kodu: {m.group(1)}'),
  (r'(?i)\bKod(?:u)?\s*[:=-]\s*([A-Z0-9][A-Z0-9_-]{3,})', lambda m:f'Kod: {m.group(1)}'),
 ]
 for pat,make in patterns:
  for m in re.finditer(pat,raw):
   x=make(m)
   if x not in out:out.append(x)
 for m in re.finditer(r'(?i)\bsepette\s+[^.!?\n]{0,120}',raw):
  x=re.sub(r'\s+',' ',m.group(0)).strip()
  if x not in out:out.append(x)
 return out[:3]
'''

if 'def campaign_lines(raw):' in s:
    cs=s.find('def campaign_lines(raw):')
    ce=s.find('\ndef process(source,b):',cs)
    if ce==-1: raise RuntimeError('campaign_lines sonu bulunamadı')
    s=s[:cs]+fn+'\n'+s[ce+1:]
else:
    s=s.replace(marker,fn+'\n'+marker,1)

# Kampanya bilgisini fiyatın altında ayrı ve görünür satır olarak ekle.
if 'for camp in campaign_lines(raw):' in s:
    import re as _re
    s=_re.sub(r"\n for camp in campaign_lines\(raw\):\n  lines\.append\('🏷️ '\+camp\)\n?",'\n',s,count=1)
marker2="\n if old and old>c:"
if marker2 not in s:
    raise RuntimeError('mesaj fiyat/kupon ekleme noktası bulunamadı')
s=s.replace(marker2,"\n for camp in campaign_lines(raw):\n  lines.append('🏷️ '+camp)"+marker2,1)

P.write_text(s,encoding='utf-8')
compile(s,str(P),'exec')
print('Fast photo + coupon/campaign preservation hardening OK')
