from hermes_cli.log_once import first_report


def test_first_report_is_true_once_per_key():
    assert first_report(("log-once-test", 1)) is True
    assert first_report(("log-once-test", 1)) is False
    assert first_report(("log-once-test", 2)) is True
