from __future__ import annotations

from pathlib import Path

from .config import Config
from .data import fetch_close, log_returns_pct
from .diagnostics import summarize
from .model import GarchTGenerator
from .plots import (
    plot_diagnostics_acf,
    plot_drawdown_comparison,
    plot_marginal_comparison,
    plot_squared_acf_comparison,
    plot_student_t_qq,
)
from .report import write_report
from .validation import validate


def main() -> None:
    cfg = Config()
    figures = cfg.output_dir / "figures"
    figures.mkdir(parents=True, exist_ok=True)

    print(f"Fetching {cfg.ticker}: {cfg.start} -> {cfg.end} (end exclusive)")
    close = fetch_close(cfg.ticker, cfg.start, cfg.end)
    returns = log_returns_pct(close)

    print(f"Loaded {len(close):,} closes and {len(returns):,} returns.")

    diagnostic_summary = summarize(returns, nlags=cfg.max_acf_lag)

    print("Fitting GARCH(1,1)-Student-t...")
    generator = GarchTGenerator().fit(returns)
    assert generator.params_ is not None

    print(
        "Fitted params:",
        {
            "mu": generator.params_.mu,
            "omega": generator.params_.omega,
            "alpha": generator.params_.alpha,
            "beta": generator.params_.beta,
            "nu": generator.params_.nu,
            "persistence": generator.params_.persistence,
        },
    )

    synthetic = generator.simulate(
        n_steps=cfg.horizon,
        n_paths=cfg.n_paths,
        seed=cfg.seed,
    )

    gates, extras = validate(
        returns,
        synthetic,
        horizon=cfg.horizon,
        acf_lags=cfg.max_acf_lag,
    )

    plot_diagnostics_acf(
        returns,
        figures / "diagnostics_acf.png",
        nlags=cfg.max_acf_lag,
    )
    plot_student_t_qq(returns, figures / "tail_qq_student_t.png")
    plot_marginal_comparison(
        returns,
        synthetic,
        figures / "marginal_comparison.png",
    )
    plot_squared_acf_comparison(
        extras["real_sq_acf"],
        extras["synthetic_sq_acf"],
        figures / "squared_acf_real_vs_synthetic.png",
    )
    plot_drawdown_comparison(
        extras["historical_drawdowns"],
        extras["synthetic_drawdowns"],
        figures / "drawdown_distribution.png",
    )

    report_path = cfg.output_dir / "validation_report.md"
    write_report(
        report_path,
        summary=diagnostic_summary,
        params=generator.params_,
        gates=gates,
    )

    passed = sum(g.passed for g in gates)
    print(f"Validation gates: {passed}/{len(gates)} passed.")
    for g in gates:
        print(f"  {'PASS' if g.passed else 'FAIL'}  {g.metric}")
    print(f"Report written to {report_path}")


if __name__ == "__main__":
    main()
