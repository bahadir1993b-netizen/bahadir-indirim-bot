from pathlib import Path
import re

P=Path('telegram_sources.py')
s=P.read_text(encoding='utf-8')

start=s.find('def _bahadir_extract_source_image(block):')
end=s.find('\ndef send(s,u,t,c,p,source,post_id,signal,coupon=None):', start)
if start!=-1 and end!=-1:
    new=r'''def _bahadir_extract_source_image(block):
    wrap=block.select_one('.tgme_widget_message_photo_wrap')
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
            except Exception as e:print('KAYNAK FOTO HREF HATA',type(e).__name__)
    img=block.select_one('.tgme_widget_message_photo img, .tgme_widget_message_photo_wrap img')
    if img:return clean(img.get('src') or img.get('data-src') or '')
    return None
'''
    s=s[:start]+new+s[end:]

s=s.replace("requests.get(image_url, headers=HEAD, timeout=10)","requests.get(image_url, headers={**HEAD,'Referer':'https://t.me/'}, timeout=10)")

P.write_text(s,encoding='utf-8')
compile(s,str(P),'exec')
print('Telegram photo hardening OK | CDN referer + photo href + og:image fallback')
