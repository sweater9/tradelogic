import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd, pytest
from tradelogic.config import Config


def make_series(n=900, drift=0.0006, vol=0.012, seed=1, start=100.0):
    rng = np.random.default_rng(seed)
    r = rng.normal(drift, vol, n)
    close = start * np.exp(np.cumsum(r))
    open_ = np.r_[start, close[:-1]] * (1 + rng.normal(0, 0.002, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.004, n)))
    vol_ = rng.integers(2_000_000, 8_000_000, n).astype(float)
    idx = pd.bdate_range("2021-01-04", periods=n)
    return pd.DataFrame(dict(open=open_, high=high, low=low, close=close, volume=vol_), index=idx)


@pytest.fixture
def cfg():
    return Config(universe=["AAA", "BBB", "CCC"], brokers={"alpaca": {"enabled": True}})


@pytest.fixture
def market():
    frames = {s: make_series(seed=i + 3, drift=0.0008) for i, s in enumerate(["AAA", "BBB", "CCC"])}
    bench = make_series(seed=99, drift=0.0006)
    return frames, bench
