import logging
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from .calendars import latest_closed, session_times
from .db import Job
from .domain import now_utc, stamp

log = logging.getLogger("marketlens.jobs")


def cycle(service, market=None):
    with service.operations:
        return _cycle(service, market)


def _cycle(service, market=None):
    from .paper import evaluate
    refreshed = service.refresh(market)
    # Settle existing orders before creating new signals from the just-closed bar.
    settled = service.job("paper:evaluate", lambda: evaluate(service))
    scan = service.scan()
    report = service.job("report:daily", lambda: service.report())
    return {"refresh": refreshed, "paper": settled, "scan_id": scan["scan_id"], "report": report}


def tick(service):
    if service.settings.data_mode not in {"yahoo", "eodhd"}:
        return
    now = now_utc()
    for market in ("ASX", "US"):
        day = latest_closed(market, now, lag_minutes=service.settings.provider_lag_minutes)
        key = f"scheduled:{market}:{day}"
        with service.db.session() as s:
            previous = s.query(Job).filter_by(key=key).order_by(Job.id.desc()).first()
            if previous and previous.status == "success":
                continue
            if previous and parse_age(previous.started_at, now) < 1800:
                continue
            # Failed jobs get a bounded safe retry; no rapid provider polling loop.
            attempts = s.query(Job).filter_by(key=key).count()
            if attempts >= 3:
                continue
        try:
            result = service.job(key, lambda market=market: cycle_checked(service, market))
        except Exception:
            log.error("Scheduled cycle failed for %s %s", market, day)
    from zoneinfo import ZoneInfo
    local = now.astimezone(ZoneInfo("Australia/Melbourne"))
    if local.weekday() >= 5:
        week_key = f"weekly:{local.strftime('%G-W%V')}"
        with service.db.session() as s:
            done = s.query(Job).filter_by(key=week_key, status="success").first()
        if not done:
            service.job(week_key, lambda: service.report(weekly=True))


def parse_age(then, now):
    from .domain import parse_time
    return (now - parse_time(then)).total_seconds()


def cycle_checked(service, market):
    result = cycle(service, market)
    if result["refresh"]["failed"]:
        raise ValueError("One or more required feeds failed; inspect Data Health before retrying")
    return result


def start_scheduler(service):
    scheduler = BackgroundScheduler(timezone="Australia/Melbourne", job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 600})
    scheduler.add_job(tick, "interval", minutes=10, args=[service], id="exchange-close-catchup", next_run_time=now_utc() + timedelta(seconds=10))
    scheduler.start()
    return scheduler
