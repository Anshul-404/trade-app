from datetime import date
from pathlib import Path

import pytest

from trading_bot.contracts import parse_nifty_futures_csv, _update_env

HEADER = (
    'SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_INSTRUMENT_NAME,SEM_SMST_SECURITY_ID,'
    'SM_SYMBOL_NAME,SEM_EXPIRY_DATE,SEM_CUSTOM_SYMBOL,SEM_LOT_UNITS\n'
)
ROWS = (
    'NSE,D,FUTIDX,55555,NIFTY,2026-11-26 15:30:00,NIFTY NOV FUT,65\n'
    'NSE,D,FUTIDX,66666,NIFTY,2026-12-31 15:30:00,NIFTY DEC FUT,65\n'
    'NSE,D,FUTIDX,77777,BANKNIFTY,2026-11-26 15:30:00,BANKNIFTY FUT,30\n'
    'NSE,D,OPTIDX,88888,NIFTY,2026-11-26 15:30:00,NIFTY CALL,65\n'
    'NSE,D,FUTIDX,33333,NIFTY,2026-10-30 15:30:00,EXPIRED NIFTY,65\n'
    'BSE,D,FUTIDX,99999,NIFTY,2026-11-26 15:30:00,BSE FUT,65\n'
)


def test_nearest_contract_filtered_and_sorted():
    c = parse_nifty_futures_csv(HEADER + ROWS, today=date(2026, 11, 13))
    assert [x.security_id for x in c] == ['55555', '66666']
    assert c[0].lot_size == 65


def test_missing_columns_fail_closed():
    with pytest.raises(ValueError, match='columns changed'):
        parse_nifty_futures_csv('foo,bar\n1,2\n')


def test_exact_underlying_matching():
    rows = ('NSE,D,FUTIDX,12345,FINNIFTY,2026-11-26,NIFTY FUTURE,20\n'
            'NSE,D,FUTIDX,12346,NIFTYNXT50,2026-11-26,NIFTY NEXT,20\n')
    assert parse_nifty_futures_csv(HEADER + rows, today=date(2026, 11, 13)) == []


def test_expired_contract_excluded():
    assert parse_nifty_futures_csv(HEADER + ROWS, today=date(2027, 1, 1)) == []


def test_env_update_preserves_credentials_and_other_keys(tmp_path: Path):
    target = tmp_path / '.env'
    target.write_text('DHAN_ACCESS_TOKEN=secret\nTB_SYMBOL=NIFTY 50\nOTHER=kept\n')
    _update_env(target, {'TB_SYMBOL': 'NIFTY FUT 2026-11-26', 'TB_SECURITY_ID': '55555'})
    result = target.read_text()
    assert 'DHAN_ACCESS_TOKEN=secret' in result
    assert 'OTHER=kept' in result
    assert 'TB_SECURITY_ID=55555' in result
    assert result.count('TB_SYMBOL=') == 1


def test_env_does_not_create_credentials_from_nothing(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        _update_env(tmp_path/'.env', {'TB_SYMBOL':'NIFTY'})
