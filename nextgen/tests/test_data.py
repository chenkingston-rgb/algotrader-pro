import json

import pandas as pd
import pytest

from trader.data import assert_exchange_sessions, assert_provider_agreement, make_signal_snapshot, restore_signal_snapshot


def prices(last=101.0):
    idx = pd.bdate_range("2025-01-01", periods=205)
    base = [100.0] * 204 + [last]
    return pd.DataFrame({s: base for s in ("SPY", "QQQ", "GLD", "BIL")}, index=idx)


def test_provider_agreement_accepts_matching_data():
    x = prices()
    assert_provider_agreement(x, x.copy(), x.index[-1])


def test_provider_agreement_rejects_close_or_signal_disagreement():
    x = prices()
    y = x.copy()
    y.loc[y.index[-1], "SPY"] = 90
    with pytest.raises(RuntimeError, match="disagree"):
        assert_provider_agreement(x, y, x.index[-1])


def test_provider_agreement_requires_exact_session_and_full_universe():
    x = prices()
    with pytest.raises(RuntimeError, match="exact"):
        assert_provider_agreement(x, x.iloc[:-1], x.index[-1])
    with pytest.raises(RuntimeError, match="every required"):
        assert_provider_agreement(x, x.drop(columns=["BIL"]), x.index[-1])


def test_exchange_session_continuity_is_exact():
    x = prices()
    assert_exchange_sessions(x, x.index[-200:], x.index[-1])
    wrong = list(x.index[-200:])
    wrong[-20] = wrong[-20] + pd.Timedelta(days=1)
    with pytest.raises(RuntimeError, match="exchange calendar"):
        assert_exchange_sessions(x, wrong, x.index[-1])


def test_signal_snapshot_is_hash_addressed_and_round_trips():
    x = prices()
    captured = pd.Timestamp("2026-01-02T15:00:00Z").to_pydatetime()
    snapshot = make_signal_snapshot(x, x.index[-1], "test", captured)
    restored = restore_signal_snapshot(snapshot)
    pd.testing.assert_frame_equal(restored, x.iloc[-200:].astype(float), check_freq=False)
    snapshot["adjusted_closes"]["SPY"][-1] += 1
    with pytest.raises(RuntimeError, match="hash mismatch"):
        restore_signal_snapshot(snapshot)


def test_signal_snapshot_survives_canonical_json_key_sorting():
    x = prices()
    captured = pd.Timestamp("2026-01-02T15:00:00Z").to_pydatetime()
    snapshot = make_signal_snapshot(x, x.index[-1], "test", captured)
    durable_copy = json.loads(json.dumps(snapshot, sort_keys=True))
    restored = restore_signal_snapshot(durable_copy)
    assert list(restored.columns) == ["SPY", "QQQ", "GLD", "BIL"]
    pd.testing.assert_frame_equal(restored, x.iloc[-200:].astype(float), check_freq=False)
