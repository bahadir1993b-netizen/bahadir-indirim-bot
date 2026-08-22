import publish_core as pc
import local_store as ls

def check(ok,msg):
    if not ok:raise AssertionError(msg)

u=pc.affiliate_url('https://www.amazon.com.tr/dp/B0TEST1234?tag=wrong&ref=x')
check('tag=ozelfirsat09-21' in u,'affiliate tag missing')
check('tag=wrong' not in u,'old affiliate tag survived')
check(pc.product_identity('https://www.amazon.com.tr/gp/product/B0TEST1234/ref=x')=='amazon:B0TEST1234','amazon identity')
check(pc.product_identity('https://www.hepsiburada.com/ornek-p-HBCV0000123')=='hepsiburada:hbcv0000123','hb identity')
check(pc.product_identity('https://www.trendyol.com/x/y-p-123456')=='trendyol:123456','trendyol identity')
check(ls.publication_key('https://www.hepsiburada.com/a-p-HBCV0000123?x=1')==ls.publication_key('https://www.hepsiburada.com/b-p-HBCV0000123?y=2'),'hb duplicate identity')
check(not pc.clean_title('Fırsat Ürünü'),'generic title must be rejected')
check(not pc.clean_title('Ürün'),'generic product title must be rejected')
check(pc.clean_title('ÖZEL FIRSATLAR - Güncel İndirimler Anker Nano 45W USB-C Şarj Cihazı #tanıtım @ozelfirsat').startswith('Anker Nano 45W'),'source signature cleanup')
print('PUBLISH CORE SELFTEST OK')
