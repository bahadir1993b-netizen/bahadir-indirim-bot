from pathlib import Path

TAG='ozelfirsat09-21'

# 1) Shared Telegram normalization: Amazon tag can never be empty.
p=Path('telegram_sources.py')
s=p.read_text(encoding='utf-8')
s=s.replace("AMAZON_TAG=os.getenv('AMAZON_ASSOCIATE_TAG','').strip()",f"AMAZON_TAG=os.getenv('AMAZON_ASSOCIATE_TAG','{TAG}').strip() or '{TAG}'")
# Re-normalize immediately before all shared Telegram sends.
s=s.replace("def send(s,u,t,c,p,source,post_id,signal,coupon=None):\n if not valid(s,u):", "def send(s,u,t,c,p,source,post_id,signal,coupon=None):\n u=normalize(s,u) or u\n if not valid(s,u):")
compile(s,str(p),'exec');p.write_text(s,encoding='utf-8')

# 2) Trusted fast lane: final URL tagging + never fallback to text-only.
p=Path('run_trusted_fast_lane.py')
s=p.read_text(encoding='utf-8')
if 'def final_url(' not in s:
    anchor="def fmt(x):return f'{x:,.2f}'.replace(',','X').replace('.',',').replace('X','.')\n"
    helper=f'''def final_url(site,url):\n    if site=='Amazon':\n        return ts.normalize('Amazon',url) or url\n    return url\n\n'''
    s=s.replace(anchor,anchor+helper,1)
s=s.replace("def send(site,url,title,current,ref,campaign,image,row):\n    disc=", "def send(site,url,title,current,ref,campaign,image,row):\n    url=final_url(site,url)\n    disc=")
# Replace text fallback with hard skip: photo is mandatory.
old="""    if not rr or not rr.ok:\n        rr=requests.post('https://api.telegram.org/bot'+ts.TOKEN+'/sendMessage',json={'chat_id':ts.CHAT,'text':text,'parse_mode':'HTML','disable_web_page_preview':True,'link_preview_options':{'is_disabled':True},'reply_markup':kb},timeout=18)\n    rr.raise_for_status()\n"""
new="""    if not rr or not rr.ok:\n        print(f'FAST ATLANDI | {site} | foto_yok/foto_gonderilemedi')\n        return False\n    rr.raise_for_status()\n"""
if old in s:s=s.replace(old,new,1)
# Require image before send so text-only can never leak.
s=s.replace("    photo=source_photo(b) or pg.get('image')\n    ok=send(", "    photo=source_photo(b) or pg.get('image')\n    if not photo:\n        print(f'FAST ATLANDI | {source}:{post_id} | foto_yok')\n        return False\n    ok=send(")
compile(s,str(p),'exec');p.write_text(s,encoding='utf-8')

# 3) Normal realtime Telegram route: re-normalize Amazon links as a final guard.
p=Path('run_telegram_realtime.py')
if p.exists():
    s=p.read_text(encoding='utf-8')
    s=s.replace("return send_clean(s,u,", "u=ts.normalize(s,u) or u\n    return send_clean(s,u,")
    compile(s,str(p),'exec');p.write_text(s,encoding='utf-8')

print('ROOT PUBLISH FIX OK | Amazon tag='+TAG+' | fast lane photo-only | final URL normalization')
