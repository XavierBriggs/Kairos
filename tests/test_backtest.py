from kairos.backtest import run_backtest
from kairos.config import BacktestConfig, FeeConfig
from kairos.synth import make_series


def test_synthetic_backtest_runs_and_reports():
    cfg = BacktestConfig(fees=FeeConfig(taker_bps=1.0))
    df = make_series(n=2000, seed=7)
    scored, report = run_backtest(cfg, df)
    assert "f_nowcast" in scored.columns
    assert (report["segment"] == "ALL").any()
    cols = {"base_mae_bp", "nowcast_mae_bp", "beats_base", "beats_carry", "nowcast_net_apr%"}
    assert cols.issubset(report.columns)


def test_nowcast_not_worse_than_persistence_overall():
    # On a DGP where the latest basis leads next-interval funding, the nowcast should
    # forecast at least as sharply as persistence on the full sample.
    cfg = BacktestConfig(fees=FeeConfig(taker_bps=1.0))
    scored, report = run_backtest(cfg, make_series(n=3000, seed=7))
    allrow = report[report["segment"] == "ALL"].iloc[0]
    assert allrow["nowcast_mae_bp"] <= allrow["base_mae_bp"] + 1e-6


def test_nowcast_beats_raw_carry_economics_overall():
    cfg = BacktestConfig(fees=FeeConfig(taker_bps=1.0))
    _, report = run_backtest(cfg, make_series(n=3000, seed=7))
    allrow = report[report["segment"] == "ALL"].iloc[0]
    assert bool(allrow["beats_carry"])  # adaptive nowcast nets >= always-collect carry
