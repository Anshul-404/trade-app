#!/usr/bin/env python3
"""Merge Trading Bot's seven existing OHLCV CSVs into ONE Excel worksheet.

Uses only Python's standard library and tqdm (already installed by Trading Bot).
Writes XLSX as streaming Office Open XML to keep RAM usage low even with
hundreds of thousands of candles. Dhan token/internet NOT required.

Usage:
    python merge_history_to_xlsx.py
    python merge_history_to_xlsx.py --dir data/historical/NIFTY50_13
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from tqdm import tqdm

TIMEFRAMES = ("1m", "2m", "5m", "15m", "60m", "1d", "1w")
COLUMNS = ("timeframe", "timestamp_IST", "epoch", "open", "high", "low", "close", "volume", "open_interest")
NEEDED = set(COLUMNS[2:]) | {"timestamp"}
EXCEL_MAX_ROWS = 1_048_576
EXCEL_EPOCH = datetime(1899, 12, 30)
IST = timezone(timedelta(hours=5, minutes=30))

CONTENT_TYPES = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>'''
ROOT_RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''
WORKBOOK = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="All_Candles" sheetId="1" r:id="rId1"/></sheets>
</workbook>'''
WORKBOOK_RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''
STYLES = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd hh:mm:ss"/></numFmts>
  <fonts count="2">
    <font><sz val="10"/><name val="Aptos"/></font>
    <font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Aptos"/></font>
  </fonts>
  <fills count="3">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FF12304A"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="3">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFill="1" applyFont="1"/>
    <xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''


def text_cell(ref: str, text: str, style: int = 0) -> str:
    # XML escaping also protects against formula injection: text is never a formula.
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t>{escape(text)}</t></is></c>'


def num_cell(ref: str, raw: str, style: int = 0) -> str:
    if raw is None or not str(raw).strip():
        return ""
    try:
        num = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Non-numeric candle field at {ref}: {raw!r}") from exc
    if not math.isfinite(num):
        return ""  # Blank when Dhan has no volume or OI.
    as_text = str(int(num)) if num.is_integer() and abs(num) < 1e15 else format(num, ".15g")
    return f'<c r="{ref}" s="{style}"><v>{as_text}</v></c>'


def date_cell(ref: str, raw: str) -> str:
    try:
        dt = datetime.fromisoformat(raw.strip())
    except ValueError as exc:
        raise ValueError(f"Unexpected timestamp in {ref}: {raw!r}") from exc
    if dt.tzinfo is not None:
        dt = dt.astimezone(IST).replace(tzinfo=None)
    serial = (dt - EXCEL_EPOCH).total_seconds() / 86400
    return f'<c r="{ref}" s="2"><v>{serial:.12f}</v></c>'


def export_excel(folder: Path | str, output: Path | str | None = None) -> tuple[Path, int]:
    folder = Path(folder).expanduser().resolve()
    paths = {tf: folder / f"{tf}.csv" for tf in TIMEFRAMES}
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing timeframe CSV(s):\n  " + "\n  ".join(missing))

    counts: dict[str, int] = {}
    for tf, path in paths.items():
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            if not reader.fieldnames or not NEEDED.issubset(reader.fieldnames):
                raise ValueError(f"Incorrect CSV columns in {path.name}. Expected: {sorted(NEEDED)}")
            counts[tf] = sum(1 for _ in reader)

    total = sum(counts.values())
    if total + 1 > EXCEL_MAX_ROWS:
        raise ValueError(
            f"All timeframes total {total:,} candles, which exceed the Excel "
            f"limit of {EXCEL_MAX_ROWS - 1:,} data rows in one worksheet."
        )

    target = Path(output).expanduser().resolve() if output else folder / f"{folder.name}_All_Candles.xlsx"
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_suffix(".xlsx.partial")

    try:
        with zipfile.ZipFile(temp_path, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as xlsx:
            for name, content in (
                ("[Content_Types].xml", CONTENT_TYPES),
                ("_rels/.rels", ROOT_RELS),
                ("xl/workbook.xml", WORKBOOK),
                ("xl/_rels/workbook.xml.rels", WORKBOOK_RELS),
                ("xl/styles.xml", STYLES),
            ):
                xlsx.writestr(name, content.encode("utf-8"))

            # Streaming ZIP member: never loads the entire 500,000-row worksheet into RAM.
            with xlsx.open("xl/worksheets/sheet1.xml", "w", force_zip64=True) as sheet, tqdm(
                total=total, desc="Building single-sheet XLSX", unit="candle", dynamic_ncols=True
            ) as bar:
                def emit(text: str) -> None:
                    sheet.write(text.encode("utf-8"))

                emit('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
                emit('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">')
                emit(f'<dimension ref="A1:I{total + 1}"/>')
                emit('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>')
                emit('<sheetFormatPr defaultRowHeight="15"/>')
                emit('<cols><col min="1" max="1" width="13" customWidth="1"/>')
                emit('<col min="2" max="2" width="23" customWidth="1"/>')
                emit('<col min="3" max="3" width="17" customWidth="1"/>')
                emit('<col min="4" max="7" width="16" customWidth="1"/>')
                emit('<col min="8" max="9" width="19" customWidth="1"/></cols>')
                emit('<sheetData>')
                emit('<row r="1" ht="24" customHeight="1">')
                for i, header in enumerate(COLUMNS):
                    emit(text_cell(f"{chr(65 + i)}1", header, style=1))
                emit('</row>')

                row_index = 2
                for tf, path in paths.items():
                    bar.set_postfix_str(tf)
                    with path.open("r", encoding="utf-8-sig", newline="") as file:
                        for csv_row in csv.DictReader(file):
                            i = row_index
                            emit(f'<row r="{i}">')
                            emit(text_cell(f"A{i}", tf))
                            emit(date_cell(f"B{i}", csv_row["timestamp"]))
                            for col_letter, col_name in zip("CDEFGHI", COLUMNS[2:]):
                                emit(num_cell(f"{col_letter}{i}", csv_row.get(col_name, "")))
                            emit('</row>')
                            row_index += 1
                            bar.update(1)

                if row_index != total + 2:
                    raise RuntimeError("CSV data changed while exporting. Please retry the export.")
                emit('</sheetData>')
                emit(f'<autoFilter ref="A1:I{total + 1}"/>')
                emit('<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>')
                emit('</worksheet>')

        os.replace(temp_path, target)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    print(f"\nExcel file: {target}")
    print(f"One worksheet: All_Candles | {total:,} candles | {target.stat().st_size / (1024 ** 2):.2f} MB")
    print("Per timeframe: " + ", ".join(f"{tf}={counts[tf]:,}" for tf in TIMEFRAMES))
    return target, total


def main() -> int:
    parser = argparse.ArgumentParser(description="Combine seven Trading Bot CSVs into ONE Excel worksheet")
    parser.add_argument("--dir", default="data/historical/NIFTY50_13", help="Folder with seven timeframe CSVs")
    parser.add_argument("--output", help="Optional output .xlsx path")
    args = parser.parse_args()
    try:
        export_excel(args.dir, args.output)
        return 0
    except (FileNotFoundError, ValueError, OSError, RuntimeError) as exc:
        parser.exit(1, f"ERROR: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
