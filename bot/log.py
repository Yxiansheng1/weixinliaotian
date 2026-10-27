from __future__ import annotations

import logging
import sys
from pathlib import Path


def setup(level: str = "INFO", log_dir: Path | None = None) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)  # type: ignore[attr-defined]
    except Exception:
        pass

    fmt = "%(asctime)s %(levelname)-7s %(message)s"
    datefmt = "%H:%M:%S"

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(
            log_dir / "bot.log", encoding="utf-8", errors="replace"
        )
        fh.setFormatter(logging.Formatter(fmt, datefmt))
        handlers.append(fh)

    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format=fmt,
        datefmt=datefmt,
        handlers=handlers,
        force=True,
    )
    # 这几个库太吵
    for noisy in ("httpx", "httpx2", "httpcore", "httpcore2", "openai", "comtypes", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
