"""The future-clock run only means something if the clock really moved."""
import datetime as dt
import os


def test_the_clock_is_at_or_past_the_requested_date_when_the_plugin_is_loaded():
    target = os.environ.get("OBSERVE_PERCEIVE_FAKE_NOW")
    if not target:
        return  # ordinary run: the real clock is in use, nothing to prove
    wanted = dt.datetime.fromisoformat(target).replace(tzinfo=dt.timezone.utc)
    assert dt.datetime.now(dt.timezone.utc) >= wanted
