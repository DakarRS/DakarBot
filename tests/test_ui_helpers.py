from dakarbot.ui import timestamp


def test_timestamp_tag():
    assert timestamp(1_000_000) == "<t:1000000:R>"
    assert timestamp(1_000_000, "f") == "<t:1000000:f>"
