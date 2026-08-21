import os,re

# Supabase proje URL'sini normalize et.
sb=os.environ.get('SUPABASE_URL','').rstrip('/')
if sb.endswith('/rest/v1'):
    sb=sb[:-8].rstrip('/')
    os.environ['SUPABASE_URL']=sb

import telegram_sources as ts

ts.MIN_DISCOUNT=15.0
ts.MAX_AGE=max(10,int(os.environ.get('TELEGRAM_MAX_AGE','10')))
ts.AMAZON_TAG=(os.environ.get('AMAZON_ASSOCIATE_TAG') or os.environ.get('AMAZON_TAG') or '').strip()

_original_send=ts.send

def strict_send(s,u,t,c,p,source,post_id,signal,coupon=None):
    # Gerçek/hesaplanabilir indirim %15'in altındaysa veya indirim oranı bilinmiyorsa paylaşma.
    if p and p>c:
        disc=(p-c)/p*100
        if disc>=ts.MIN_DISCOUNT:
            return _original_send(s,u,t,c,p,source,post_id,signal,coupon)
        try: ts.remember(f'{source}:{post_id}')
        except Exception: pass
        print(f'ATLANDI | {source}:{post_id} | kesin indirim %{disc:.1f} < %{ts.MIN_DISCOUNT}')
        return False

    # 3 al 2 öde / 2 al 1 öde gibi matematiksel olarak net kampanyalar.
    m=re.search(r'\b(\d+)\s*al\s*(\d+)\s*(?:öde|ode)\b',signal or '',re.I)
    if m:
        buy,paid=int(m.group(1)),int(m.group(2))
        if buy>paid>0:
            disc=(buy-paid)/buy*100
            if disc>=ts.MIN_DISCOUNT:
                effective=c*(paid/buy)
                return _original_send(s,u,t,effective,c,source,post_id,signal,coupon)

    try: ts.remember(f'{source}:{post_id}')
    except Exception: pass
    print(f'ATLANDI | {source}:{post_id} | %15+ doğrulanabilir indirim yok')
    return False

ts.send=strict_send

print(f'=== Telegram gerçek-zamanlı tarama | eşik=%15 | yaş={ts.MAX_AGE} dk ===')
ts.main()
