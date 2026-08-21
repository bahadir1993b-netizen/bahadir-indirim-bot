from pathlib import Path
import re

p=Path('telegram_fast_sources.py')
s=p.read_text(encoding='utf-8')
start=s.find('def send_photo_or_text(')
end=s.find('\ndef process(',start)
if start < 0 or end < 0:
    raise SystemExit('send_photo_or_text/process bulunamadı')
new=r'''def send_photo_or_text(text,u,image,source,post_id):
    markup={'inline_keyboard':[[{'text':'🛒 FIRSATA GİT','url':u}]]}
    # Fotoğraf bulunduysa önce Telegram'a URL değil gerçek dosya gönderiyoruz.
    # Böylece Telegram CDN/Referer kaynaklı fotoğraf reddi metin gönderisine dönüşmeden önce çözülür.
    if image:
        try:
            ir=requests.get(image,headers={**HEAD,'Referer':'https://t.me/'},timeout=12,allow_redirects=True)
            ctype=(ir.headers.get('content-type') or '').lower()
            if ir.ok and len(ir.content)>1000 and ('image/' in ctype or ir.content[:3]==b'\xff\xd8\xff' or ir.content[:8]==b'\x89PNG\r\n\x1a\n' or ir.content[:4]==b'RIFF'):
                r=requests.post('https://api.telegram.org/bot'+TOKEN+'/sendPhoto',data={'chat_id':CHAT,'caption':text[:1024],'reply_markup':json.dumps(markup,ensure_ascii=False)},files={'photo':('telegram-source.jpg',ir.content,ctype or 'image/jpeg')},timeout=20)
                r.raise_for_status()
                print(f'GÖRSEL GÖNDERİLDİ | {source}:{post_id} | bytes={len(ir.content)}')
                return
            print(f'GÖRSEL İNDİRİLEMEDİ | {source}:{post_id} | http={ir.status_code} | type={ctype} | bytes={len(ir.content)}')
        except Exception as e:
            print(f'GÖRSEL HATA | {source}:{post_id} | {type(e).__name__}: {e}')
    requests.post('https://api.telegram.org/bot'+TOKEN+'/sendMessage',json={'chat_id':CHAT,'text':text,'disable_web_page_preview':False,'reply_markup':markup},timeout=10).raise_for_status()
'''
s=s[:start]+new+s[end:]
p.write_text(s,encoding='utf-8')
compile(s,str(p),'exec')
print('Final Telegram photo guard OK')
