from pathlib import Path
import re

p = Path('marketplace_scanner.py')
s = p.read_text(encoding='utf-8')
# Existing marketplace_scanner remains the Amazon engine. Hepsiburada/Trendyol
# are handled exclusively by marketplace_nonamazon_scanner.py so the two
# engines cannot publish the same product twice.
new_markets = '''MARKETS = {
    'Amazon': ('https://www.amazon.com.tr/s?k=', 'amazon.com.tr', re.compile(r'/(?:dp|gp/product)/[A-Z0-9]{8,}', re.I)),
}'''
s2, n = re.subn(r'MARKETS = \{.*?\n\}', new_markets, s, count=1, flags=re.S)
if n != 1:
    raise RuntimeError('MARKETS bloğu bulunamadı')
p.write_text(s2, encoding='utf-8')
compile(s2, str(p), 'exec')
print('AMAZON ONLY RUNTIME OK | mevcut marketplace motorunda yalnızca Amazon çalışıyor | HB/Trendyol ayrı motorda')
