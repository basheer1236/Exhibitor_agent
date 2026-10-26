"""
Pure-Python Excel (.xlsx) and CSV exporter for extracted exhibitor records.
Requires zero external dependencies (built directly with standard library zipfile and XML).
"""

import csv
import io
import xml.sax.saxutils
import zipfile
from typing import Any, Sequence


EXCEL_COLUMNS = [
    ("company_name", "Company Name"),
    ("website", "Website"),
    ("email", "Primary Email"),
    ("emails", "All Emails"),
    ("phone", "Primary Phone"),
    ("phones", "All Phones"),
    ("booth", "Booth / Stand"),
    ("hall", "Hall"),
    ("address", "Address"),
    ("city", "City"),
    ("state", "State"),
    ("country", "Country"),
    ("category", "Categories"),
    ("products", "Products"),
    ("description", "Description"),
    ("detail_url", "Detail Page URL"),
    ("source_url", "Source Directory URL"),
    ("confidence", "Extraction Confidence"),
]


def _format_cell_value(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, (list, set, tuple)):
        return ", ".join(str(item) for item in val if item is not None)
    return str(val)


def _col_idx_to_name(col_idx: int) -> str:
    """Converts 0 -> A, 1 -> B, 25 -> Z, 26 -> AA, etc."""
    result = ""
    while col_idx >= 0:
        result = chr(col_idx % 26 + 65) + result
        col_idx = col_idx // 26 - 1
    return result


def records_to_csv(records: Sequence[Any]) -> str:
    """Exports records to a standard CSV string."""
    output = io.StringIO()
    headers = [label for _, label in EXCEL_COLUMNS]
    keys = [key for key, _ in EXCEL_COLUMNS]
    
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(headers)
    
    for r in records:
        row = []
        for k in keys:
            if isinstance(r, dict):
                v = r.get(k)
            else:
                v = getattr(r, k, None)
            row.append(_format_cell_value(v))
        writer.writerow(row)
        
    return output.getvalue()


def records_to_xlsx(records: Sequence[Any]) -> bytes:
    """
    Generates a genuine Microsoft Excel OpenXML (.xlsx) spreadsheet binary
    without third-party libraries using standard library zipfile.
    Includes styled dark header with white bold text and auto-width estimation.
    """
    buf = io.BytesIO()
    keys = [key for key, _ in EXCEL_COLUMNS]
    headers = [label for _, label in EXCEL_COLUMNS]

    # Calculate approximate column character widths
    col_widths = [len(h) for h in headers]
    formatted_rows = []
    for r in records:
        row = []
        for i, k in enumerate(keys):
            if isinstance(r, dict):
                v = r.get(k)
            else:
                v = getattr(r, k, None)
            s_val = _format_cell_value(v)
            row.append(s_val)
            col_widths[i] = max(col_widths[i], min(len(s_val), 60))
        formatted_rows.append(row)

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # [Content_Types].xml
        zf.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>""")

        # _rels/.rels
        zf.writestr("_rels/.rels", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""")

        # xl/_rels/workbook.xml.rels
        zf.writestr("xl/_rels/workbook.xml.rels", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>""")

        # xl/workbook.xml
        zf.writestr("xl/workbook.xml", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Exhibitor Directory" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>""")

        # xl/styles.xml (Professional dark slate header, zebra rows, clean borders)
        zf.writestr("xl/styles.xml", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="3">
    <font><name val="Segoe UI"/><sz val="10"/><color rgb="FF1E293B"/></font>
    <font><b/><name val="Segoe UI"/><sz val="10"/><color rgb="FFFFFFFF"/></font>
    <font><name val="Segoe UI"/><sz val="10"/><color rgb="FF0F172A"/></font>
  </fonts>
  <fills count="4">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FF0F172A"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFF8FAFC"/></patternFill></fill>
  </fills>
  <borders count="2">
    <border><left/><right/><top/><bottom/></border>
    <border>
      <left style="thin"><color rgb="FFE2E8F0"/></left>
      <right style="thin"><color rgb="FFE2E8F0"/></right>
      <top style="thin"><color rgb="FFE2E8F0"/></top>
      <bottom style="thin"><color rgb="FFE2E8F0"/></bottom>
    </border>
  </borders>
  <cellStyleXfs count="1">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>
  </cellStyleXfs>
  <cellXfs count="4">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>
    <xf numFmtId="0" fontId="2" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1"/>
    <xf numFmtId="0" fontId="2" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>
  </cellXfs>
</styleSheet>""")

        # xl/worksheets/sheet1.xml
        cols_xml = []
        for i, w in enumerate(col_widths):
            cols_xml.append(f'<col min="{i+1}" max="{i+1}" width="{max(w + 3, 12)}" customWidth="1"/>')
        cols_str = f'<cols>{"".join(cols_xml)}</cols>'

        sheet_lines = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
            cols_str,
            '<sheetData>',
        ]

        # Header Row (Row 1, Style 1 = Dark background + Bold White font)
        row_idx = 1
        h_cells = []
        for c_idx, h in enumerate(headers):
            c_ref = f"{_col_idx_to_name(c_idx)}{row_idx}"
            esc = xml.sax.saxutils.escape(h)
            h_cells.append(f'<c r="{c_ref}" t="inlineStr" s="1"><is><t>{esc}</t></is></c>')
        sheet_lines.append(f'<row r="{row_idx}" ht="24" customHeight="1">{"".join(h_cells)}</row>')

        # Data Rows (Alternating zebra background: Style 2 vs Style 3)
        for row_data in formatted_rows:
            row_idx += 1
            style_id = "3" if (row_idx % 2 == 0) else "2"
            r_cells = []
            for c_idx, val in enumerate(row_data):
                c_ref = f"{_col_idx_to_name(c_idx)}{row_idx}"
                esc = xml.sax.saxutils.escape(val)
                r_cells.append(f'<c r="{c_ref}" t="inlineStr" s="{style_id}"><is><t>{esc}</t></is></c>')
            sheet_lines.append(f'<row r="{row_idx}" ht="20" customHeight="1">{"".join(r_cells)}</row>')

        sheet_lines.append('</sheetData>')
        sheet_lines.append('</worksheet>')

        zf.writestr("xl/worksheets/sheet1.xml", "\n".join(sheet_lines))

    return buf.getvalue()
