from pathlib import Path
import re

p=Path('bot.py')
s=p.read_text(encoding='utf-8')

# Never treat an arbitrary higher price elsewhere on the page as the old price.
s=s.replace('    if not previous and ps:\n        higher=[x for x in ps if x>current*1.05]\n        if higher:previous=min(higher)\n', '')
s=s.replace('        higher=[x for x in ps if current and x>current*1.05];previous=min(higher) if higher else None\n', '        previous=None\n')

# Capture the marketplace-declared discount and use the page title when JSON-LD is missing.
old='    return {"name":re.sub(r"\\s+"," ",name or "Ürün").strip()[:300],"price":current,"previous_display_price":previous if previous and previous>current else None,"campaign_price":campaign if campaign and campaign<current else None,"coupon_code":coupon,"campaign_note":None,"url":url,"site":site}\n'
new='    declared_discount=None\n    dm=re.search(r"(\\d+(?:[.,]\\d+)?)\\s*%\\s*(?:indirim|tasarruf|discount)",text,re.I)\n    if dm:\n        try: declared_discount=float(dm.group(1).replace(",","."))\n        except: pass\n    return {"name":re.sub(r"\\s+"," ",name or "Ürün").strip()[:300],"price":current,"previous_display_price":previous if previous and previous>current else None,"campaign_price":campaign if campaign and campaign<current else None,"coupon_code":coupon,"campaign_note":None,"declared_discount":declared_discount,"url":url,"site":site}\n'
if old in s:
    s=s.replace(old,new)
else:
    raise SystemExit('make_product return pattern not found')

old='        return make_product(site,jd.get("name") or title,url,text,current,previous) or make_product(site,title,url,text,current,previous)\n'
new='        page_title=page.title() or title or "Ürün"\n        return make_product(site,jd.get("name") or page_title,url,text,current,previous) or make_product(site,page_title,url,text,current,previous)\n'
if old in s:
    s=s.replace(old,new)
else:
    raise SystemExit('page_product return pattern not found')

old='    discount=((baseline-current)/baseline*100) if baseline and baseline>current else 0\n    print(f"DEĞERLENDİR: {p[\'site\']} | {current:.2f} TL | baz={baseline} | indirim=%{discount:.1f}")\n'
new='    discount=((baseline-current)/baseline*100) if baseline and baseline>current else 0\n    declared=p.get("declared_discount")\n    if declared is not None:\n        discount=min(discount, float(declared)) if discount else float(declared)\n        if discount < MIN_DISCOUNT:\n            print(f"RED: {p[\'site\']} sayfanın ilan ettiği indirim %{declared:.1f}; eşik %{MIN_DISCOUNT:.1f}")\n    print(f"DEĞERLENDİR: {p[\'site\']} | {current:.2f} TL | baz={baseline} | indirim=%{discount:.1f}")\n'
if old in s:
    s=s.replace(old,new)
else:
    raise SystemExit('process discount pattern not found')

p.write_text(s,encoding='utf-8')
print('runtime patch applied')
