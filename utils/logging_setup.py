"""Centralized logger factory: console (human-readable) + daily JSONL file."""

import json
import logging
import sys
import traceback
from datetime import datetime
from pathlib import Path


class _JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        doc: dict = {
            "ts": datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "name": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            doc["exc"] = traceback.format_exception(*record.exc_info)
        if record.stack_info:
            doc["stack"] = record.stack_info
        return json.dumps(doc, ensure_ascii=False)


def setup_logger(
    name: str,
    log_dir: str = "./logs",
    level: str = "INFO",
) -> logging.Logger:
    """
    Return a named logger with two handlers:
      - StreamHandler(stdout): human-readable timestamps
      - FileHandler: one JSONL file per day under log_dir/

    Idempotent: if the logger already has handlers it is returned as-is,
    so calling this multiple times with the same name is safe.
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    # Console handler
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    )
    logger.addHandler(sh)

    # Daily JSONL file handler
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    fh = logging.FileHandler(
        log_path / f"{name}_{stamp}.jsonl", encoding="utf-8"
    )
    fh.setFormatter(_JsonLineFormatter())
    logger.addHandler(fh)

    return logger
