from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path


def configure_logging(logs_dir: Path, level: str = "INFO") -> str:
    logs_dir.mkdir(parents=True, exist_ok=True)
    runs_dir = logs_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    app_log = logs_dir / "app.log"
    errors_log = logs_dir / "errors.log"
    run_log = runs_dir / f"{run_id}.log"

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s [%(name)s] run_id=%(run_id)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    class RunIdFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            if not hasattr(record, "run_id"):
                record.run_id = run_id
            return True

    for log_path, handler_level in ((app_log, logging.INFO), (run_log, logging.DEBUG), (errors_log, logging.ERROR)):
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setLevel(handler_level)
        handler.setFormatter(formatter)
        handler.addFilter(RunIdFilter())
        root.addHandler(handler)

    console = logging.StreamHandler()
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    console.setFormatter(formatter)
    console.addFilter(RunIdFilter())
    root.addHandler(console)

    return run_id
