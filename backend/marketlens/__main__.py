import argparse
import json

from .config import settings
from .db import Database
from .jobs import cycle
from .service import Service

parser = argparse.ArgumentParser(description="MarketLens local research operations")
parser.add_argument("command", choices=["init", "refresh", "scan", "cycle", "report", "weekly", "health"])
args = parser.parse_args()
service = Service(settings, Database(settings.database_url))
service.initialise()
commands = {"init": lambda: {"ready": True, "mode": settings.data_mode}, "refresh": service.refresh, "scan": service.scan,
            "cycle": lambda: cycle(service), "report": service.report, "weekly": lambda: service.report(True), "health": service.data_health}
print(json.dumps(commands[args.command](), indent=2, allow_nan=False))
