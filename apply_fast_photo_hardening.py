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
P.write_text(s,encoding='utf-8')
compile(s,str(P),'exec')
print('Fast photo hardening OK')
