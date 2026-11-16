"""Read-only Dhan instrument lookup and explicit local Trading Bot chart selection.

Never guess NSE futures security IDs. Current security IDs and lot sizes must
come from Dhan's instrument master, not from stale constants.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import re
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

CSV_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
IST = ZoneInfo("Asia/Kolkata")
ENV_PATH = Path(__file__).resolve().parent / ".env"
MAX_CSV_BYTES = 55_000_000
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


def _value(row: dict[str, str | None], name: str) -> str:
    return (row.get(name) or "").strip()


def _nifty_name(name: str) -> bool:
    """Accept NIFTY 50 aliases without including BANKNIFTY / NIFTYNXT50."""
    normalized = re.sub(r"[\s_\-]+", " ", name.strip().upper())
    return normalized in {
        "NIFTY", "NIFTY50", "NIFTY 50", "NIFTY INDEX",
        "NIFTY 50 INDEX", "NIFTY50 INDEX",
    }


def _expiry_date(value: str) -> date | None:
    value = value.strip()
    if not value:
        return None
    # Dhan commonly uses ISO dates, sometimes followed by an intraday time.
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        pass
    # Additional date formats found in broker/export variants.
    token = re.split(r"[ T]", value, maxsplit=1)[0].strip()
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(token, fmt).date()
        except ValueError:
            continue
    return None


def scan_nifty_futures_csv(content: str, today: date | None = None):
    """Return (contracts, counters, examples) to make failed lookups diagnosable."""
    cutoff = today or datetime.now(IST).date()
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    fields = {field.strip() for field in (reader.fieldnames or []) if field}
    missing = REQUIRED_COLUMNS - fields
    if missing:
        raise ValueError(f"Dhan security master columns changed; missing: {', '.join(sorted(missing))}")

    counts: Counter[str] = Counter()
    examples: list[dict[str, str]] = []
    contracts: dict[tuple[str, date], Contract] = {}
    for row in reader:
        counts["rows"] += 1
        if _value(row, "SEM_EXM_EXCH_ID").upper() != "NSE":
            continue
        if _value(row, "SEM_SEGMENT").upper() != "D":
            continue
        counts["nse_derivatives"] += 1
        if _value(row, "SEM_INSTRUMENT_NAME").upper() != "FUTIDX":
            continue
        counts["nse_index_futures"] += 1
        underlying = _value(row, "SM_SYMBOL_NAME")
        display = _value(row, "SEM_CUSTOM_SYMBOL")
        trading = _value(row, "SEM_TRADING_SYMBOL")
        if len(examples) < 8 and "NIFTY" in f"{underlying} {display} {trading}".upper():
            examples.append({
                "underlying": underlying[:65],
                "display": display[:75],
                "trading": trading[:65],
                "expiry_raw": _value(row, "SEM_EXPIRY_DATE")[:40],
                "security_id": _value(row, "SEM_SMST_SECURITY_ID")[:24],
            })
        # Trust the underlying symbol when Dhan provides it.
# Fall back to the trading symbol only when underlying is blank.
        if underlying.strip():
            is_nifty = _nifty_name(underlying)
        else:
            is_nifty = trading.upper().startswith("NIFTY-")

        if not is_nifty:
            continue

        counts["nifty_name_matches"] += 1
        expiry = _expiry_date(_value(row, "SEM_EXPIRY_DATE"))
        if expiry is None:
            counts["invalid_expiry_format"] += 1
            continue
        counts["valid_expiry_formats"] += 1
        if expiry < cutoff:
            counts["expired"] += 1
            continue
        counts["unexpired"] += 1
        sid = _value(row, "SEM_SMST_SECURITY_ID")
        lot_raw = _value(row, "SEM_LOT_UNITS")
        try:
            lot_size = int(float(lot_raw))
        except (ValueError, OverflowError):
            counts["invalid_id_or_lot"] += 1
            continue
        if not sid.isdecimal() or not 0 < lot_size < 1_000_000:
            counts["invalid_id_or_lot"] += 1
            continue
        contract = Contract(sid, display or f"NIFTY FUT {expiry.isoformat()}", expiry, lot_size)
        contracts[(sid, expiry)] = contract
        counts["valid_contract_rows"] += 1
    return sorted(contracts.values(), key=lambda c: (c.expiry, int(c.security_id))), counts, examples


def parse_nifty_futures_csv(content: str, today: date | None = None) -> list[Contract]:
    return scan_nifty_futures_csv(content, today)[0]


def _fetch_csv(timeout: int = 30) -> str:
    request = Request(CSV_URL, headers={"User-Agent": "TradingBot/0.3 (+instrument-master-lookup)"})
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(MAX_CSV_BYTES + 1)
    if len(raw) > MAX_CSV_BYTES:
        raise RuntimeError("Dhan instrument CSV exceeded the 55MB safety limit.")
    return raw.decode("utf-8-sig", errors="replace")


def download_contracts(timeout: int = 30) -> list[Contract]:
    contracts, counts, _ = scan_nifty_futures_csv(_fetch_csv(timeout))
    if not contracts:
        raise RuntimeError(
            "No active NIFTY futures passed the lookup filters. "
            f"NSE FUTIDX rows: {counts['nse_index_futures']}; "
            f"NIFTY name matches: {counts['nifty_name_matches']}; "
            f"valid expiry formats: {counts['valid_expiry_formats']}; "
            f"unexpired: {counts['unexpired']}. Run --diagnose to inspect matching rows."
        )
    return contracts


def _update_env(env_path: Path, items: dict[str, str]) -> None:
    """Atomic update of only specific chart keys; leave credentials intact."""
    if not env_path.exists():
        raise FileNotFoundError(f"Missing {env_path}. Create it from .env.example.")
    lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    result: list[str] = []
    seen: set[str] = set()
    for line in lines:
        match = re.match(r"^([A-Z][A-Z0-9_]*)\s*=", line)
        if match and match.group(1) in items:
            key = match.group(1)
            if key not in seen:
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
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.writelines(result)
        os.replace(tmp, env_path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List / select Dhan NIFTY index futures")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--nearest", action="store_true", help="Save nearest listed NIFTY futures contract")
    group.add_argument("--select", metavar="SECURITY_ID", help="Save a listed security ID")
    group.add_argument("--restore-index", action="store_true", help="Restore NIFTY 50 spot chart")
    group.add_argument("--diagnose", action="store_true", help="Show filter counts and sample rows")
    parser.add_argument("--csv", type=Path, help="Optional local copy of Dhan compact CSV")
    args = parser.parse_args(argv)

    if args.restore_index:
        _update_env(ENV_PATH, {"TB_EXCHANGE": "IDX_I", "TB_INSTRUMENT": "INDEX", "TB_SECURITY_ID": "13", "TB_SYMBOL": "NIFTY 50"})
        print("Configured NIFTY 50 spot. Restart Uvicorn.")
        return 0

    try:
        content = args.csv.read_text(encoding="utf-8-sig") if args.csv else _fetch_csv()
        contracts, counts, samples = scan_nifty_futures_csv(content)
    except Exception as exc:
        print(f"Contract lookup failed: {type(exc).__name__}: {exc}")
        return 1

    if args.diagnose or not contracts:
        print("Dhan instrument master diagnostics (no credentials involved):")
        for name in ("rows", "nse_derivatives", "nse_index_futures", "nifty_name_matches", "invalid_expiry_format", "valid_expiry_formats", "expired", "unexpired", "invalid_id_or_lot", "valid_contract_rows"):
            print(f"  {name}: {counts[name]}")
        print("  Sample NSE FUTIDX rows mentioning NIFTY (not proof of exact underlying):")
        for item in samples:
            print(f"    {item}")
        if not contracts:
            print("No valid active contract found. Configuration NOT changed.")
            return 1
        if args.diagnose:
            return 0

    print("Active NSE NIFTY 50 index futures from Dhan:")
    for c in contracts:
        print(f"  ID {c.security_id:<10} Expiry {c.expiry} Lot {c.lot_size:<4} {c.display_name}")
    if args.nearest or args.select:
        selected = contracts[0] if args.nearest else next((c for c in contracts if c.security_id == args.select), None)
        if selected is None:
            print("Unknown or expired security ID: configuration NOT changed.")
            return 2
        _update_env(ENV_PATH, {
            "TB_EXCHANGE": "NSE_FNO", "TB_INSTRUMENT": "FUTIDX",
            "TB_SECURITY_ID": selected.security_id,
            "TB_SYMBOL": f"NIFTY FUT {selected.expiry.isoformat()}",
        })
        print(f"Selected {selected.display_name} (security ID {selected.security_id}). Restart Uvicorn.")
    else:
        print("To switch the chart: python -m trading_bot.contracts --nearest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
