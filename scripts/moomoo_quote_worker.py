"""Isolated quote worker. Reads JSON on stdin; no account or trading interface."""
import json
import re
import sys

PREFIX = 'MARKETLENS_QUOTE_RESULT:'


def collect(request, sdk=None):
    codes = request.get('codes', [])
    if not 1 <= len(codes) <= 20 or any(not isinstance(c, str) or not re.fullmatch(r'US\.[A-Z0-9][A-Z0-9.\-]{0,19}', c) for c in codes):
        return {'status': 'invalid_request', 'message': 'Use 1–20 US quote symbols'}
    if not isinstance(request.get('port'), int) or not 1024 <= request['port'] <= 65535:
        return {'status': 'invalid_request', 'message': 'Invalid local OpenD port'}
    if sdk is None:
        import moomoo as sdk
    sdk.SysConfig.set_all_thread_daemon(True)
    sdk.SysConfig.set_client_info('MarketLens quote research', 1)
    context = None
    try:
        context = sdk.OpenQuoteContext(host='127.0.0.1', port=request['port'])
        ret, state = context.get_global_state()
        if ret != sdk.RET_OK or not state.get('qot_logined'):
            return {'status': 'login_required', 'message': 'Sign into Moomoo OpenD and wait for quote-server login'}
        ret, _ = context.subscribe(codes, [sdk.SubType.K_5M], subscribe_push=False, session=sdk.Session.ALL)
        if ret != sdk.RET_OK:
            return {'status': 'subscription_denied', 'message': 'US all-session 5-minute subscription was denied; check API entitlements and quote quotas in OpenD'}
        result = {'status': 'success', 'gateway_version': str(state.get('server_ver', 'unknown'))[:50],
                  'quote_login': True, 'interval': '5m', 'session_request': 'ALL', 'adjustment': 'NONE', 'symbols': {}}
        for code in codes:
            ret, frame = context.get_cur_kline(code, 300, ktype=sdk.SubType.K_5M, autype=sdk.AuType.NONE)
            if ret != sdk.RET_OK:
                result['symbols'][code] = {'status': 'unavailable', 'rows': []}
                continue
            fields = ['code', 'time_key', 'open', 'high', 'low', 'close', 'volume']
            if not all(f in frame.columns for f in fields):
                result['symbols'][code] = {'status': 'invalid_response', 'rows': []}
                continue
            result['symbols'][code] = {'status': 'success', 'rows': json.loads(frame[fields].to_json(orient='records'))}
        return result
    except Exception:
        return {'status': 'gateway_error', 'message': 'The local quote request failed; inspect OpenD connectivity and permissions'}
    finally:
        if context is not None:
            context.close()


if __name__ == '__main__':
    try:
        value = collect(json.loads(sys.stdin.read(8192)))
    except (Exception, SystemExit):
        value = {'status': 'sdk_error', 'message': 'The optional Moomoo SDK could not load or complete the quote request'}
    print(PREFIX + json.dumps(value, allow_nan=False), flush=True)
