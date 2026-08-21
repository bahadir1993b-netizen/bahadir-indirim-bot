from pathlib import Path
import re

P = Path('marketplace_scanner.py')
s = P.read_text(encoding='utf-8')
marker = '⭐️ BOTUN BULDUĞU FIRSAT'

if marker not in s:
    # Marketplace tarayıcısının Telegram mesajındaki ilk başlığı bulup
    # bunun kaynak kanallardan değil, botun bağımsız aramasından geldiğini belirt.
    patterns = [
        r"(msg\s*=\s*f?['\"])(🔥)",
        r"(message\s*=\s*f?['\"])(🔥)",
        r"(text\s*=\s*f?['\"])(🔥)",
    ]
    changed = False
    for pat in patterns:
        s2, n = re.subn(pat, r"\1⭐️ BOTUN BULDUĞU FIRSAT\\n\\n\2", s, count=1)
        if n:
            s = s2
            changed = True
            break
    if not changed:
        raise SystemExit('Marketplace Telegram mesajı bulunamadı; güvenli şekilde durduruldu.')
    P.write_text(s, encoding='utf-8')
    compile(s, str(P), 'exec')
    print('Kendi bulduğu fırsat etiketi eklendi: ⭐️ BOTUN BULDUĞU FIRSAT')
else:
    print('Kendi bulduğu fırsat etiketi zaten mevcut.')
