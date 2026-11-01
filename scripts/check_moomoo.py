"""Connection diagnostic without touching the research database."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from marketlens.config import settings
from marketlens.moomoo import MoomooBridge, normalise_bar
from marketlens.domain import now_utc, stamp

result = MoomooBridge(settings).fetch(['AAPL.US'])
accepted = 0
if result['status'] == 'success':
    for row in result.get('symbols', {}).get('US.AAPL', {}).get('rows', []):
        try:
            accepted += normalise_bar(row, 'AAPL.US', now_utc()) is not None
        except (ValueError, TypeError, KeyError):
            pass
print(json.dumps({'checked_at': stamp(), 'status': result['status'], 'validated_completed_bars': accepted,
                  'message': result.get('message', 'Quote response received; validate actual session coverage before use')}))
sys.exit(0 if accepted else 2)
