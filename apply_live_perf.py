"""Apply the Trading Bot performance patch with backup/rollback.

Read-only data/UI optimization. Does not touch .env or orders.
"""
from datetime import datetime
from pathlib import Path
import runpy


def main():
    root = Path(__file__).resolve().parent
    tracked = [
        'trading_bot/app.py', 'trading_bot/candles.py',
        'trading_bot/static/app.js', 'trading_bot/static/zones_layer.js',
        'trading_bot/tests/test_fast_stream.py',
    ]
    if not (root / 'trading_bot' / 'app.py').exists():
        raise SystemExit('Expected trading_bot/app.py in current project. Extract ZIP to project root.')
    if 'def latest_bars(' in (root/'trading_bot/candles.py').read_text(encoding='utf8'):
        print('This patch is already installed; nothing changed.')
        return
    backup_dir = root / 'backups' / ('live_perf_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
    original = {}
    for rel in tracked:
        source = root / rel
        if rel != 'trading_bot/tests/test_fast_stream.py' and not source.is_file():
            raise SystemExit(f'Missing file: {source}')
        original[rel] = source.read_bytes() if source.is_file() else None
    backup_dir.mkdir(parents=True, exist_ok=True)
    for rel, data in original.items():
        if data is not None:
            dest = backup_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
    print('Backups saved to:', backup_dir)
    try:
        runpy.run_path(str(root / 'performance_payload.py'))
    except Exception:
        for rel, data in original.items():
            target = root / rel
            if data is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(data)
        print('Patch failed: original files restored. Please share the error output.')
        raise


if __name__ == '__main__':
    main()
