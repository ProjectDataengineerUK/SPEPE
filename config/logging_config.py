import logging
import sys
from pathlib import Path


def setup_logging(
    log_level: str = "INFO",
    console_log_level: str = "WARNING",
    enable_console: bool = True,
    log_dir: str = "output/logs",
) -> None:
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(log_level.upper())

    fmt = logging.Formatter(
        "%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    file_handler = logging.FileHandler(f"{log_dir}/spepe.log", encoding="utf-8")
    file_handler.setLevel(log_level.upper())
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    if enable_console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(console_log_level.upper())
        console_handler.setFormatter(fmt)
        root.addHandler(console_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
