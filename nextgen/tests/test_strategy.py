import itertools

import pandas as pd
import pytest

from trader.config import CORE_QQQ_WEIGHT, DYNAMIC_SLEEVE_WEIGHT
from trader.strategy import SignalDataError, decide


def frame(spy=101.0, qqq=101.0, gld=101.0, bil=100.0):
    idx = pd.bdate_range("2025-01-01", periods=205)
    base = [100.0] * 204
    return pd.DataFrame(
        {"SPY": base + [spy], "QQQ": base + [qqq], "GLD": base + [gld], "BIL": base + [bil]},
        index=idx,
    )


def test_all_on_has_growth_core_plus_three_dynamic_sleeves():
    x = frame()
    d = decide(x, x.index[-1])
    assert d.weights["SPY"] == pytest.approx(DYNAMIC_SLEEVE_WEIGHT)
    assert d.weights["QQQ"] == pytest.approx(CORE_QQQ_WEIGHT + DYNAMIC_SLEEVE_WEIGHT)
    assert d.weights["GLD"] == pytest.approx(DYNAMIC_SLEEVE_WEIGHT)
    assert d.weights["BIL"] == pytest.approx(0)


def test_all_off_preserves_qqq_core_and_moves_dynamic_capital_to_bil():
    x = frame(spy=99.0, qqq=99.0, gld=99.0)
    assert decide(x, x.index[-1]).weights == pytest.approx(
        {"SPY": 0.0, "QQQ": CORE_QQQ_WEIGHT, "GLD": 0.0, "BIL": 0.80}
    )


@pytest.mark.parametrize("states", list(itertools.product([False, True], repeat=3)))
def test_every_trend_state_maps_to_exact_frozen_weights(states):
    px = [101.0 if on else 99.0 for on in states]
    x = frame(*px)
    weights = decide(x, x.index[-1]).weights
    expected = {
        "SPY": DYNAMIC_SLEEVE_WEIGHT if states[0] else 0.0,
        "QQQ": CORE_QQQ_WEIGHT + (DYNAMIC_SLEEVE_WEIGHT if states[1] else 0.0),
        "GLD": DYNAMIC_SLEEVE_WEIGHT if states[2] else 0.0,
    }
    expected["BIL"] = 1.0 - sum(expected.values())
    assert weights == pytest.approx(expected)


def test_no_lookahead():
    x = frame(spy=101.0)
    signal_date = x.index[-2]
    a = decide(x, signal_date)
    y = x.copy()
    y.iloc[-1] = 1_000_000.0
    assert a == decide(y, signal_date)


def test_missing_window_fails_closed():
    x = frame()
    x.loc[x.index[-10], "GLD"] = None
    with pytest.raises(SignalDataError):
        decide(x, x.index[-1])


def test_exact_signal_session_is_required():
    x = frame()
    with pytest.raises(SignalDataError):
        decide(x, x.index[-1] + pd.Timedelta(days=1))
