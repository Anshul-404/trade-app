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

from trading_bot.contracts import _expiry_date, _nifty_name, scan_nifty_futures_csv


def test_common_expiry_date_variants():
    for value in ['2026-11-24', '2026-11-24 15:30:00', '2026-11-24T15:30:00',
                  '24-NOV-2026', '24-Nov-2026', '24/11/2026', '24-11-2026',
                  '2026/11/24']:
        assert _expiry_date(value) == date(2026, 11, 24)
    assert _expiry_date('not-a-date') is None


def test_safe_underlying_aliases():
    assert _nifty_name('NIFTY 50 INDEX')
    assert _nifty_name('NIFTY_50')
    for name in ('BANKNIFTY', 'NIFTYNXT50', 'NIFTY NEXT 50', 'FINNIFTY', 'MIDCPNIFTY'):
        assert not _nifty_name(name)


def test_non_iso_expiry_is_accepted():
    row = 'NSE,D,FUTIDX,12345,NIFTY 50 INDEX,24-NOV-2026,NIFTY NOV FUT,65\n'
    c = parse_nifty_futures_csv(HEADER + row, today=date(2026, 11, 13))
    assert len(c) == 1 and c[0].security_id == '12345'


def test_malformed_expiry_diagnostic():
    row = 'NSE,D,FUTIDX,12345,NIFTY,tomorrow,NIFTY NOV FUT,65\n'
    c, stats, examples = scan_nifty_futures_csv(HEADER + row, today=date(2026, 11, 13))
    assert c == [] and stats['nifty_name_matches'] == 1
    assert stats['invalid_expiry_format'] == 1
    assert examples[0]['expiry_raw'] == 'tomorrow'


def test_never_select_other_nifty_family_indices():
    rows = (
        'NSE,D,FUTIDX,11111,NIFTY NEXT 50,24-NOV-2026,NIFTY NEXT FUT,65\n'
        'NSE,D,FUTIDX,22222,NIFTY 50 INDEX,24-NOV-2026,NIFTY NOV FUT,65\n'
    )
    c = parse_nifty_futures_csv(HEADER + rows, today=date(2026, 11, 13))
    assert [x.security_id for x in c] == ['22222']


def test_live_dhan_blank_underlying_nifty_and_other_indices():
    header = (
        'SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_INSTRUMENT_NAME,SEM_SMST_SECURITY_ID,'
        'SM_SYMBOL_NAME,SEM_EXPIRY_DATE,SEM_CUSTOM_SYMBOL,SEM_LOT_UNITS,SEM_TRADING_SYMBOL\n'
    )
    rows = (
        'NSE,D,FUTIDX,54746,,2027-01-25 14:30:00,BANKNIFTY JAN FUT,30,BANKNIFTY-Jan2027-FUT\n'
        'NSE,D,FUTIDX,54747,,2027-01-25 14:30:00,FINNIFTY JAN FUT,60,FINNIFTY-Jan2027-FUT\n'
        'NSE,D,FUTIDX,54748,,2027-01-25 14:30:00,MIDCPNIFTY JAN FUT,120,MIDCPNIFTY-Jan2027-FUT\n'
        'NSE,D,FUTIDX,54749,,2027-01-25 14:30:00,NIFTY JAN FUT,65,NIFTY-Jan2027-FUT\n'
        'NSE,D,FUTIDX,54750,,2027-01-25 14:30:00,NIFTYFPI JAN FUT,65,NIFTYFPI-Jan2027-FUT\n'
        'NSE,D,FUTIDX,54751,,2027-01-25 14:30:00,NIFTYNXT50 JAN FUT,65,NIFTYNXT50-Jan2027-FUT\n'
        'NSE,D,FUTIDX,58872,,2026-12-29 14:30:00,BANKNIFTY DEC FUT,30,BANKNIFTY-Dec2026-FUT\n'
        'NSE,D,FUTIDX,58873,,2026-12-29 14:30:00,FINNIFTY DEC FUT,60,FINNIFTY-Dec2026-FUT\n'
    )
    contracts, counts, _ = scan_nifty_futures_csv(header + rows, today=date(2026, 11, 13))
    assert [c.security_id for c in contracts] == ['54749']
    assert counts['nifty_name_matches'] == 1


def test_blank_underlying_exact_display_name_supported():
    csv_text = HEADER + 'NSE,D,FUTIDX,12345,,2026-11-26,NIFTY NOV FUT,65\n'
    assert [c.security_id for c in parse_nifty_futures_csv(csv_text, date(2026, 11, 13))] == ['12345']


def test_nonempty_other_underlying_cannot_be_overridden_by_display():
    csv_text = HEADER + 'NSE,D,FUTIDX,12345,BANKNIFTY,2026-11-26,NIFTY NOV FUT,65\n'
    assert parse_nifty_futures_csv(csv_text, date(2026, 11, 13)) == []
