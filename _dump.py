import xml.etree.ElementTree as ET, json
import os

os.chdir(r'd:\aishu\code\anyshare-sync')

ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
tree = ET.parse('_tmp_xlsx/xl/sharedStrings.xml')
strings = [t.text or '' for t in tree.getroot().findall('.//s:t', ns)]
tree2 = ET.parse('_tmp_xlsx/xl/worksheets/sheet1.xml')
rows = tree2.getroot().findall('.//s:row', ns)

cols_order = []
for row in rows[0:2]:
    for c in row.findall('s:c', ns):
        ref = c.get('r', '')
        cl = ''.join(ch for ch in ref if ch.isalpha())
        if cl not in cols_order: cols_order.append(cl)

result = {'total_rows': len(rows), 'columns': cols_order, 'headers': {}, 'data': []}
for i, row in enumerate(rows[:3]):
    cells = {}
    for c in row.findall('s:c', ns):
        ref = c.get('r')
        v = c.find('s:v', ns)
        t = c.get('t', '')
        val = v.text if v is not None else ''
        if t == 's' and val: val = strings[int(val)] if int(val) < len(strings) else val
        cells[ref] = val
    if i == 2:
        result['headers'] = {cl: cells.get(f'{cl}{row.get("r")}','') for cl in cols_order}
    else:
        result['data'].append({cl: cells.get(f'{cl}{row.get("r")}','') for cl in cols_order})

for row in rows[3:33]:
    cells = {}
    for c in row.findall('s:c', ns):
        ref = c.get('r')
        v = c.find('s:v', ns)
        t = c.get('t', '')
        val = v.text if v is not None else ''
        if t == 's' and val: val = strings[int(val)] if int(val) < len(strings) else val
        cells[ref] = val
    result['data'].append({cl: cells.get(f'{cl}{row.get("r")}','') for cl in cols_order})

with open('_xlsx_data.json', 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print(f"OK - {len(result['data'])} rows written")
