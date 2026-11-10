from hermes_cli.log_once import first_report


def test_first_report_is_true_once_per_key():
    assert first_report(("log-once-test", 1)) is True
    assert first_report(("log-once-test", 1)) is False
    assert first_report(("log-once-test", 2)) is True


def test_warn_once_downgrades_repeats_to_debug(caplog):
    import logging

    from hermes_cli.log_once import warn_once

    log = logging.getLogger("log-once-test")
    with caplog.at_level(logging.DEBUG, logger="log-once-test"):
        warn_once(log, "warn-once-key", "again %s", 1)
        warn_once(log, "warn-once-key", "again %s", 2)

    assert [r.levelno for r in caplog.records] == [logging.WARNING, logging.DEBUG]
