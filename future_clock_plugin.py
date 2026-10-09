"""Pytest plugin: run the suite on a clock set in the future.

Several tests passed on the day they were written and failed weeks later,
because a fixed date aged past a window (the governance chain flags an event
time more than 30 days before ingestion). Running the suite once on a clock
set well ahead of today makes the next such test fail the day it is written.

Not loaded by default. CI loads it for a second run:

    OBSERVE_PERCEIVE_FAKE_NOW=2027-02-01 python -m pytest -q -p future_clock_plugin

The clock keeps ticking from that date, so timing code still behaves.
Requires freezegun (in the ``test`` extra).
"""
from __future__ import annotations

import os

_ENV = "OBSERVE_PERCEIVE_FAKE_NOW"
_freezer = None


def pytest_configure(config):
    global _freezer
    target = os.environ.get(_ENV)
    if not target:
        raise RuntimeError(f"future_clock_plugin is loaded but {_ENV} is not set")
    from freezegun import freeze_time

    _freezer = freeze_time(target, tick=True)
    _freezer.start()


def pytest_unconfigure(config):
    global _freezer
    if _freezer is not None:
        _freezer.stop()
        _freezer = None
