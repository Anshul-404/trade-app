"""Resolve current NIFTY index futures from Dhan's official security master.

All actions are read-only except an explicit local .env configuration update.
Never hardcode derivative Security IDs across expiries.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

CSV_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
IST = ZoneInfo("Asia/Kolkata")
ENV_PATH = Path(__file__).resolve().parent / ".env"
REQUIRED_COLUMNS = {
    "SEM_EXM_EXCH_ID", "SEM_SEGMENT", "SEM_INSTRUMENT_NAME",
    "SEM_SMST_SECURITY_ID", "SM_SYMBOL_NAME", "SEM_EXPIRY_DATE",
    "SEM_CUSTOM_SYMBOL", "SEM_LOT_UNITS",
}


@dataclass(frozen=True)
class Contract:
    security_id: str
    display_name: str
    expiry: date
    lot_size: int


def parse_nifty_futures_csv(content: str, today: date | None = None) -> list[Contract]:
    """Filter active NSE NIFTY 50 futures by expiry, without mixing other indices."""
    cutoff = today or datetime.now(IST).date()
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames or not REQUIRED_COLUMNS.issubset(reader.fieldnames):
        raise ValueError("Dhan security master columns changed; cannot safely identify contracts.")
    contracts: dict[tuple[str, date], Contract] = {}
    for row in reader:
        if row["SEM_EXM_EXCH_ID"].strip().upper() != "NSE":
            continue
        if row["SEM_SEGMENT"].strip().upper() != "D":
            continue
        if row["SEM_INSTRUMENT_NAME"].strip().upper() != "FUTIDX":
            continue
        underlying = re.sub(r"\s+", " ", row["SM_SYMBOL_NAME"].strip().upper())
        # Exact match prevents including BANKNIFTY, FINNIFTY, NIFTYNXT50 etc.
        if underlying not in {"NIFTY", "NIFTY 50", "NIFTY50"}:
            continue
        try:
            expiry = date.fromisoformat(row["SEM_EXPIRY_DATE"].strip()[:10])
            security_id = row["SEM_SMST_SECURITY_ID"].strip()
            lot_size = int(float(row["SEM_LOT_UNITS"].strip()))
        except (ValueError, TypeError):
            continue
        if expiry < cutoff or not security_id.isdigit() or lot_size < 1:
            continue
        symbol = row["SEM_CUSTOM_SYMBOL"].strip() or f"NIFTY FUT {expiry}"
        contract = Contract(security_id, symbol, expiry, lot_size)
        contracts[(security_id, expiry)] = contract
    return sorted(contracts.values(), key=lambda c: (c.expiry, int(c.security_id)))


def download_contracts(timeout: int = 25) -> list[Contract]:
    req = Request(CSV_URL, headers={"User-Agent": "TradingBot/0.2 (read-only; Dhan official instruments)"})
    with urlopen(req, timeout=timeout) as response:
        # Guard unexpected unbounded responses; CSV is normally many MB.
        raw = response.read(55_000_001)
    if len(raw) > 55_000_000:
        raise RuntimeError("Instrument CSV exceeded the safe size limit.")
    text = raw.decode("utf-8-sig", errors="replace")
    contracts = parse_nifty_futures_csv(text)
    if not contracts:
        raise RuntimeError("No active NIFTY contracts found. Dhan format may have changed.")
    return contracts


def _update_env(env_path: Path, items: dict[str, str]) -> None:
    """Update specified keys only; preserve existing credentials; atomic replacement."""
    if not env_path.exists():
        raise FileNotFoundError(f"Missing {env_path}. Create it from .env.example first.")
    content = env_path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        match = re.match(r"^([A-Z][A-Z0-9_]*)\s*=", line)
        if match and match.group(1) in items:
            key = match.group(1)
            if key in seen:
                continue
            result.append(f"{key}={items[key]}\n")
            seen.add(key)
        else:
            result.append(line)
    for key, value in items.items():
        if key not in seen:
            if result and not result[-1].endswith("\n"):
                result.append("\n")
            result.append(f"{key}={value}\n")
    fd, tmp = tempfile.mkstemp(prefix=".env.tmp.", dir=str(env_path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.writelines(result)
        os.replace(tmp, env_path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Find/select Dhan NIFTY 50 futures for Trading Bot")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--nearest", action="store_true", help="Apply nearest listed NIFTY future")
    group.add_argument("--select", metavar="SECURITY_ID", help="Apply a specific listed contract ID")
    group.add_argument("--restore-index", action="store_true", help="Switch back to NIFTY 50 spot index")
    args = parser.parse_args(argv)
    if args.restore_index:
        _update_env(ENV_PATH, {
            "TB_EXCHANGE": "IDX_I", "TB_INSTRUMENT": "INDEX",
            "TB_SECURITY_ID": "13", "TB_SYMBOL": "NIFTY 50",
        })
        print("Configured NIFTY 50 spot. Restart Uvicorn to apply.")
        return 0
    try:
        contracts = download_contracts()
    except Exception as exc:
        print(f"Contract lookup failed: {type(exc).__name__}: {exc}")
        return 1
    print("Official Dhan security master — NIFTY 50 FUTIDX (active):")
    for c in contracts:
        print(f"  ID {c.security_id:<10} Expiry {c.expiry.isoformat()}  Lot {c.lot_size:<4} {c.display_name}")
    if args.nearest or args.select:
        if args.nearest:
            selected = contracts[0]
        else:
            selected = next((c for c in contracts if c.security_id == args.select), None)
            if selected is None:
                print("Refusing to set an ID not found in the current instrument master.")
                return 2
        _update_env(ENV_PATH, {
            "TB_EXCHANGE": "NSE_FNO", "TB_INSTRUMENT": "FUTIDX",
            "TB_SECURITY_ID": selected.security_id,
            "TB_SYMBOL": f"NIFTY FUT {selected.expiry.isoformat()}",
        })
        print(f"\nSelected: {selected.display_name} (ID {selected.security_id})")
        print("Local .env updated. Restart Uvicorn to switch the chart feed.")
    else:
        print("\nTo switch: python -m trading_bot.contracts --nearest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
