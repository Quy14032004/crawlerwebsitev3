import re

with open('app/templates/index.html', 'r', encoding='utf-8', errors='ignore') as f:
    text = f.read()

pos = 0
out = []
target = 'cdha'
for m in re.finditer(r'cdha', text, re.IGNORECASE):
    p = m.start()
    out.append(f"--- MATCH AT {p} ---")
    out.append(text[max(0, p-100):min(len(text), p+1500)])

with open('scratch/cdha_tab_dump.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(out))

print("Dumped", len(out)//2, "matches")
