with open("scratch_after_click.html", "r", encoding="utf-8") as f:
    html = f.read()

import re
matches = re.findall(r'.{0,100}feed force.{0,100}', html, re.I)
print("Feed force matches:", len(matches))
for m in matches:
    print("  -", m.strip())

matches_size = re.findall(r'.{0,100}Size.{0,100}', html, re.I)
print("Size matches:", len(matches_size))
for m in matches_size[:5]:
    print("  -", m.strip())
