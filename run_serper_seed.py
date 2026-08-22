import os,time,requests,re
from datetime import datetime,timezone
import archive_store as ar

KEY=(os.environ.get('SERPER_API_KEY') or '').strip();LIMIT=max(1,int(os.environ.get('SERPER_SEED_QUERIES','12')))
QUERIES=['elektronik indirim','akıllı saat indirim','kulaklık fırsat','tablet indirim','televizyon indirim','küçük ev aletleri indirim','kişisel bakım indirim','bebek ürünleri indirim','kırtasiye indirim','market temizlik indirim','oyuncak indirim','mutfak ürünleri indirim']

def money(x):
    s=re.sub(r'[^0-9,.]','',str(x or ''))
    if not s:return None
    if ',' in s and '.' in s:s=s.replace('.','').replace(',','.') if s.rfind(',')>s.rfind('.') else s.replace(',','')
    elif ',' in s:
        a,b=s.rsplit(',',1);s=a.replace('.','')+'.'+b if len(b)<=2 else s.replace(',','')
    elif '.' in s:
        a,b=s.rsplit('.',1);s=s.replace('.','') if len(b)>2 else s
    try:return float(s)
    except:return None

def main():
    if not KEY:
        print('SERPER SEED: anahtar yok, ücretsiz kaynaklarla devam');return
    used=saved=0
    for q in QUERIES[:LIMIT]:
        try:
            r=requests.post('https://google.serper.dev/shopping',headers={'X-API-KEY':KEY,'Content-Type':'application/json'},json={'q':q,'gl':'tr','hl':'tr','num':40},timeout=20);used+=1
            if r.status_code==400 and 'Not enough credits' in r.text:
                print(f'SERPER SEED: kredi bitti | kullanılan_sorgu={used-1} | sistem ücretsiz kaynaklarla devam');break
            if not r.ok:
                print(f'SERPER SEED HTTP {r.status_code}: {r.text[:120]}');continue
            for x in r.json().get('shopping',[]):
                title=x.get('title') or '';p=money(x.get('price') or x.get('priceText'));src=x.get('source') or 'SerperShopping';url=x.get('link') or ''
                if title and p and ar.add(title,p,'',None,src,'comparison-current',url,datetime.now(timezone.utc).isoformat()):saved+=1
            print(f'SERPER SEED | {q} | kayıt={saved}');time.sleep(.2)
        except Exception as e:print(f'SERPER SEED HATA {type(e).__name__}: {e}')
    print(f'SERPER SEED BİTTİ | sorgu={used} | kayıt={saved} | arsiv={ar.stats()}')

if __name__=='__main__':main()
