"""Test the command-line full flow with a simulated Dhan API (NO NETWORK)."""
import os
import sys
import tempfile
import pandas as pd
import dhan_history as m


def mock_api(session, endpoint, body):
    start = pd.Timestamp(body["fromDate"], tz=m.IST)
    end = pd.Timestamp(body["toDate"], tz=m.IST)
    timestamps = []
    for date in pd.date_range(start.normalize(), end.normalize(), freq="D"):
        if date.weekday() < 5:
            if endpoint == "historical":
                t = date + pd.Timedelta(hours=9, minutes=15)
                if start <= t < end:
                    timestamps.append(t)
            else:
                for minute in range(6):
                    t = date + pd.Timedelta(hours=9, minutes=15 + minute)
                    if start <= t < end:
                        timestamps.append(t)
    n = len(timestamps)
    return {"status": "success", "data": {
        "timestamp": [int(t.timestamp()) for t in timestamps],
        "open": [100.0] * n,
        "high": [101.0] * n,
        "low": [99.0] * n,
        "close": [100.5] * n,
        "volume": [10] * n,
        "open_interest": [0] * n,
    }}


if __name__ == "__main__":
    m.api_post = mock_api
    os.environ["DHAN_ACCESS_TOKEN"] = "OFFLINE_TEST_TOKEN"
    with tempfile.TemporaryDirectory() as temp:
        sys.argv = ["dhan_history.py", "--days", "30", "--out", temp]
        result = m.main()
        assert result == 0, f"main returned {result}"
        directory = __import__("pathlib").Path(temp) / "NIFTY50_13"
        for tf in ("1m", "2m", "5m", "15m", "60m", "1d", "1w"):
            df = pd.read_csv(directory / f"{tf}.csv")
            assert not df.empty, f"No {tf} bars"
            assert list(df.columns) == ["timestamp", "epoch", "open", "high", "low", "close", "volume", "open_interest"]
        from zipfile import ZipFile
        from xml.etree import ElementTree
        excel_file = directory / "NIFTY50_13_All_Candles.xlsx"
        assert excel_file.is_file(), "Missing combined single-sheet workbook"
        with ZipFile(excel_file) as z:
            assert z.testzip() is None
            sheet_xml = ElementTree.fromstring(z.read("xl/worksheets/sheet1.xml"))
            ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            assert len(sheet_xml.findall(".//x:sheetData/x:row", ns)) == sum(
                len(pd.read_csv(directory / f"{tf}.csv"))
                for tf in ("1m", "2m", "5m", "15m", "60m", "1d", "1w")
            ) + 1
        # --excel-only must NOT require a valid Dhan token.
        os.environ.pop("DHAN_ACCESS_TOKEN", None)
        sys.argv = ["dhan_history.py", "--excel-only", "--out", temp]
        assert m.main() == 0
    print("Full workflow simulated with all seven CSVs plus one XLSX worksheet: PASS")
