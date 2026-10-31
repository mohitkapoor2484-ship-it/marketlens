import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from marketlens.config import ROOT, Settings
from marketlens.db import Database
from marketlens.service import Service


@pytest.fixture(scope="session")
def service(tmp_path_factory):
    root = tmp_path_factory.mktemp("marketlens")
    config = Settings(_env_file=None, data_mode="demo", database_url=f"sqlite:///{root.as_posix()}/test.db", universe_path=ROOT / "universe.json", report_dir=root / "reports", scheduler_enabled=False)
    svc = Service(config, Database(config.database_url))
    svc.initialise()
    return svc
