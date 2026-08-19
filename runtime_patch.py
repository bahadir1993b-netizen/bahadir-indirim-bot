from pathlib import Path

p = Path('bot.py')
s = p.read_text(encoding='utf-8')

# The previous patch accidentally wrote literal \\n sequences into bot.py.
# Convert only the Akakce block's escaped newlines back to real source newlines.
start = s.find(r'\n\ndef akakce_check(')
if start != -1:
    end = s.find(r'\n\ndef process(p):', start)
    if end == -1:
        raise SystemExit('Akakce block end not found')
    block = s[start:end]
    block = block.replace(r'\n', '\n')
    s = s[:start] + block + s[end:]

# If the escaped block was already absent, ensure the function is present in valid Python.
if 'def akakce_check(' not in s:
    raise SystemExit('Akakce function missing')

p.write_text(s, encoding='utf-8')
print('runtime patch applied: Akakce syntax fixed')