from pathlib import Path
import re

P = Path('telegram_sources.py')
s = P.read_text(encoding='utf-8')

new_sources = "SOURCES={'OnuAl':'onual_firsat','EnesOzen':'enesozen','OzelFirsatlar':'ozelfirsat','AmazonOzel':'amazonozel','FirsatZ':'firsatz','FirsatMerkezi':'firsatmerkez','IndirimDeal':'indirimdeal','YurticiFirsat':'yurticifirsat','IndirimAlarmi':'indirimalarmi_tr','FirsatDolu':'firsatdolu','IndirimBuluyoruz':'indirimbuldumtg'}"
s, n = re.subn(r"SOURCES=\{.*?\}", new_sources, s, count=1, flags=re.S)
if n != 1:
    raise SystemExit('SOURCES bulunamadı')

# Kaynak taraması mümkün olduğunca hızlı ve süreklilik içinde çalışsın.
s = s.replace("MAX_AGE=30", "MAX_AGE=30\nSOURCE_PRIORITY=True\nSOURCE_LIMIT=12")

# Aynı kanalın tek çalışmada gereksiz yere çok fazla mesajı tüketmesini önle.
if "SOURCE_LIMIT" in s and "SOURCE_LIMIT not in" not in s:
    s = s.replace("for b in blocks:", "for b in blocks[:SOURCE_LIMIT]:")

P.write_text(s, encoding='utf-8')
print('Priority patch OK | reklam kaynakları önce | 11 kaynak | kaynak başına limit 12')
