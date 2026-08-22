from pathlib import Path
import re

p=Path('run_telegram_realtime.py')
s=p.read_text(encoding='utf-8')

# Affiliate etiketi hiçbir zaman boş kalmasın.
s=s.replace("ts.AMAZON_TAG=(os.environ.get('AMAZON_ASSOCIATE_TAG') or os.environ.get('AMAZON_TAG') or '').strip()",
            "ts.AMAZON_TAG=(os.environ.get('AMAZON_ASSOCIATE_TAG') or os.environ.get('AMAZON_TAG') or 'ozelfirsat09-21').strip() or 'ozelfirsat09-21'")

# Telegram realtime da ortak duplicate/yayın hafızasına bağlansın.
if 'import local_store as ls' not in s:
    s=s.replace('import telegram_sources as ts\n','import telegram_sources as ts\nimport local_store as ls\n',1)

# Fotoğraf kaynak Telegram mesajından değil, doğrudan satış sayfasından alınır.
new_photo=r'''def source_photo(source,post_id,product_url=None,page_image=None):
    if page_image and str(page_image).startswith('http'):
        return page_image
    if product_url:
        try:
            r=requests.get(product_url,headers=ts.HEAD,timeout=12,allow_redirects=True)
            if r.ok:
                soup=BeautifulSoup(r.text,'html.parser')
                for sel,attr in [('#landingImage','data-old-hires'),('#landingImage','src'),('meta[property="og:image"]','content'),('meta[name="twitter:image"]','content'),('img[itemprop="image"]','src')]:
                    e=soup.select_one(sel)
                    if e:
                        u=html.unescape(e.get(attr) or '')
                        if u.startswith('http'):return u
                e=soup.select_one('img.a-dynamic-image')
                if e:
                    try:
                        d=json.loads(e.get('data-a-dynamic-image') or '{}')
                        if d:return next(iter(d.keys()))
                    except Exception:pass
        except Exception:pass
    return None
'''
s=re.sub(r'def source_photo\(source,post_id,product_url=None,page_image=None\):.*?\n(?=def fmt\()',new_photo+'\n',s,flags=re.S)

# Aynı ürün başka servisçe yeni paylaşılmışsa realtime da tekrar basmasın.
s=s.replace("def product_recently_posted(url,current):\n    key=_canonical_product_key(url)",
            "def product_recently_posted(url,current):\n    if ls.recently_published(url,current,days=30,min_drop=.05):return True\n    key=_canonical_product_key(url)")

# Başlık artıkları.
s=s.replace("text=re.sub(r'\\s+',' ',text).strip(' -|•')",
            "text=re.sub(r'\\s*[:|\\-]?\\s*Amazon\\.com\\.tr\\s*:\\s*.*$',' ',text,flags=re.I)\n    text=re.sub(r'(?i)^\\s*(?:sepete ekleniyor|sepete ekle|hemen al)\\s*[.!…-]*\\s*',' ',text)\n    text=re.sub(r'\\s+',' ',text).strip(' -|•:')")

# Başarılı realtime gönderimini ortak publish log'a yaz.
s=s.replace("rr.raise_for_status()\n    if isinstance(row,dict)","rr.raise_for_status()\n    ls.mark_published(u,c,'telegram-realtime')\n    if isinstance(row,dict)")

p.write_text(s,encoding='utf-8')
compile(s,str(p),'exec')
print('build safety patch OK')
