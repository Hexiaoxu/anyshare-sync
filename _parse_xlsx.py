import xml.etree.ElementTree as ET

ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
tree = ET.parse('_tmp_xlsx/xl/sharedStrings.xml')
strings = [t.text or '' for t in tree.getroot().findall('.//s:t', ns)]

tree2 = ET.parse('_tmp_xlsx/xl/worksheets/sheet1.xml')
rows = tree2.getroot().findall('.//s:row', ns)

cols_order = []
for row in rows[0:1]:
    for c in row.findall('s:c', ns):
        ref = c.get('r', '')
        col_letter = ''.join(ch for ch in ref if ch.isalpha())
        if col_letter not in cols_order:
            cols_order.append(col_letter)

print(f"Total rows: {len(rows)}, Total shared strings: {len(strings)}")
print(f"Columns: {cols_order}")
print()

for row in rows[:10]:
    cells = {}
    for c in row.findall('s:c', ns):
        ref = c.get('r')
        v = c.find('s:v', ns)
        t = c.get('t', '')
        val = v.text if v is not None else ''
        if t == 's' and val:
            val = strings[int(val)] if int(val) < len(strings) else val
        cells[ref] = val

    line_parts = []
    for col in cols_order:
        cell_ref = f"{col}{row.get('r')}"
        val = cells.get(cell_ref, '')
        if isinstance(val, str) and len(val) > 60:
            val = val[:57] + '...'
        line_parts.append(f"{col}={val}")
    print(' | '.join(line_parts))
