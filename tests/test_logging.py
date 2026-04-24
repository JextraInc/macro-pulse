import json
import logging as stdlog

from macropulse.logging import configure_logging, get_logger


def test_logger_emits_json(capsys):
    configure_logging(level="INFO")
    log = get_logger("test")
    log.info("hello", post_id="abc", compound=-0.7)
    out = capsys.readouterr().out.strip().splitlines()[-1]
    parsed = json.loads(out)
    assert parsed["event"] == "hello"
    assert parsed["post_id"] == "abc"
    assert parsed["compound"] == -0.7
    assert "ts" in parsed and "level" in parsed
    # reset root so other tests aren't affected
    stdlog.getLogger().handlers.clear()
