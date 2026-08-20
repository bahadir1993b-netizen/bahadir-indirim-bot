from pathlib import Path
import re

P=Path('marketplace_scanner.py')
s=P.read_text(encoding='utf-8')

needle="BAD_PRICE_CONTEXT=re.compile(r'(?:kupon|kod|kazan[çc]|avantaj|indirim|tasarruf|kargo|shipping|aylık|ayda|/ay|x\\s*ay|taksit|puan|cashback|bonus|hediye)',re.I)"
if 'BLOCKED_PRODUCT=' not in s:
    s=s.replace(needle, needle+"\nBLOCKED_PRODUCT=re.compile(r'\\b(kitap|kitapları|roman|dergi|magazin|e-kitap|ebook|yayınevi|yayıncılık)\\b',re.I)")

s=s.replace("  title=clean_title(anchor_text)\n  all_prices=prices(card_text)", "  title=clean_title(anchor_text)\n  if BLOCKED_PRODUCT.search(title):return None\n  all_prices=prices(card_text)")
s=s.replace("  if previous<=current:return None\n  disc=(previous-current)/previous*100", "  if previous<=current:return None\n  if previous>current*4:return None\n  disc=(previous-current)/previous*100")
s=s.replace("  if len(title)<10:return None\n  return disc,normalize(site,href),title,current,previous", "  if len(title)<10 or BLOCKED_PRODUCT.search(title):return None\n  return disc,normalize(site,href),title,current,previous")
s=s.replace("  if not title:title=(soup.title.get_text(' ',strip=True) if soup.title else fallback_title)\n", "  if not title:title=(soup.title.get_text(' ',strip=True) if soup.title else fallback_title)\n  if BLOCKED_PRODUCT.search(title):return None\n")

# Crucial: if the detail page has no real old-price element, do not fall back to the search-card's untrusted previous price.
s=s.replace("  previous=max(old or [x for x in current_vals if x>current],default=None)\n  # Do not trust the search-card previous price unless it is close enough to current.\n  if not previous or previous<=current:previous=expected_previous\n  if not previous or previous<=current:return None", "  previous=max(old or [x for x in current_vals if x>current],default=None)\n  if not previous or previous<=current:return None")

P.write_text(s,encoding='utf-8')
print('Scanner runtime patch: güvenilmez önceki fiyatlar reddediliyor + kitaplar hariç')
