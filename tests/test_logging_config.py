from __future__ import annotations

import logging
import re

from trade_xquant.logging_config import configure_logging


def test_console_logging_includes_timestamp(tmp_path, capsys) -> None:
    configure_logging(str(tmp_path / "gateway.jsonl"))

    logging.getLogger("trade_xquant.test").info("daemon event")

    captured = capsys.readouterr()
    assert re.match(
        r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} INFO daemon event\n$",
        captured.err,
    )
