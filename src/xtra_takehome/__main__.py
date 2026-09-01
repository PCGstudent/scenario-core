from __future__ import annotations

import json

from .challenger import GjrSkewTGenerator
from .config import Config
from .data import fetch_close, log_returns_pct
from .diagnostics import summarize
from .plots import (
    plot_diagnostics_acf,
    plot_drawdown_comparison,
    plot_marginal_comparison,
    plot_squared_acf_comparison,
    plot_student_t_qq,
    plot_tail_diagnostics,
    plot_year_severity,
)
from .report import write_report
from .validation import (
    exceedance_checks,
    explosive_path_sensitivity,
    validate,
    validate_horizon_matched,
)
from .windows import compute_window_stats, rolling_blocks, stress_episode_span


def main() -> None:
    cfg = Config()
    figures = cfg.output_dir / "figures"
    figures.mkdir(parents=True, exist_ok=True)

    print(f"Fetching {cfg.ticker}: {cfg.start} -> {cfg.end} (end exclusive)")
    close = fetch_close(cfg.ticker, cfg.start, cfg.end)
    returns = log_returns_pct(close)
    print(f"Loaded {len(close):,} closes and {len(returns):,} returns.")

    diagnostic_summary = summarize(returns, nlags=cfg.max_acf_lag)

    print("Fitting selected GJR-GARCH(1,1,1)-skew-t generator...")
    generator = GjrSkewTGenerator().fit(returns)
    assert generator.params_ is not None
    assert generator.fit_summary_ is not None
    p = generator.params_

    print(
        "Fitted params:",
        {
            "mu": p.mu,
            "omega": p.omega,
            "alpha": p.alpha,
            "gamma": p.gamma,
            "beta": p.beta,
            "eta": p.eta,
            "lambda": p.lam,
            "effective_persistence": p.effective_persistence,
            "implied_unconditional_volatility": p.implied_unconditional_variance**0.5,
            "fourth_moment_coefficient": p.fourth_moment_coefficient,
        },
    )

    synthetic = generator.simulate(
        n_steps=cfg.horizon,
        n_paths=cfg.n_paths,
        seed=cfg.seed,
    )

    # Family 1: pooled marginal check.
    gates, extras = validate(
        returns,
        synthetic,
        horizon=cfg.horizon,
        acf_lags=cfg.max_acf_lag,
    )

    # Family 2 and 3: year-level statistics estimated on equal-length blocks.
    print(f"Estimating year-level statistics on {cfg.horizon}-day blocks...")
    real_stats = compute_window_stats(
        rolling_blocks(returns.to_numpy(), cfg.horizon), acf_lags=cfg.max_acf_lag
    )
    synthetic_stats = compute_window_stats(synthetic, acf_lags=cfg.max_acf_lag)
    matched_gates = validate_horizon_matched(real_stats, synthetic_stats)
    exceedances = exceedance_checks(real_stats, synthetic_stats, len(returns))
    leave_out = explosive_path_sensitivity(synthetic, synthetic_stats.volatility)
    stress_episode = stress_episode_span(
        real_stats.es99, returns.index[: real_stats.n_blocks], "ES 99%", quantile=0.95
    )

    plot_diagnostics_acf(returns, figures / "diagnostics_acf.png", nlags=cfg.max_acf_lag)
    plot_student_t_qq(returns, figures / "tail_qq_student_t.png")
    plot_tail_diagnostics(returns, figures / "tail_index_and_mean_excess.png")
    plot_marginal_comparison(returns, synthetic, figures / "marginal_comparison.png")
    plot_squared_acf_comparison(
        extras["real_sq_acf"],
        real_stats,
        synthetic_stats,
        figures / "squared_acf_real_vs_synthetic.png",
    )
    plot_year_severity(real_stats, synthetic_stats, figures / "year_severity.png")
    plot_drawdown_comparison(
        extras["historical_drawdowns"],
        extras["synthetic_drawdowns"],
        figures / "drawdown_distribution.png",
    )

    fit_summary_path = cfg.output_dir / "fit_summary.txt"
    fit_summary_path.write_text(generator.fit_summary_ + "\n", encoding="utf-8")

    manifest = {
        "data": {
            "ticker": cfg.ticker,
            "start": cfg.start,
            "end_exclusive": cfg.end,
            "n_closes": int(len(close)),
            "n_returns": int(len(returns)),
            "return_definition": "100 * log(P_t / P_{t-1})",
        },
        "model": {
            "name": "GJR-GARCH(1,1,1) with Hansen skewed-t innovations",
            "parameters": {
                "mu": p.mu,
                "omega": p.omega,
                "alpha": p.alpha,
                "gamma": p.gamma,
                "beta": p.beta,
                "eta": p.eta,
                "lambda": p.lam,
                "effective_persistence": p.effective_persistence,
                "implied_unconditional_volatility": p.implied_unconditional_variance
                ** 0.5,
                "fourth_moment_coefficient": p.fourth_moment_coefficient,
            },
        },
        "simulation": {
            "seed": cfg.seed,
            "horizon_trading_days": cfg.horizon,
            "n_paths": cfg.n_paths,
            "initialization": "sampled historical fitted residual/variance states",
            "rng": "numpy SeedSequence(seed).spawn(2): independent state and innovation streams",
        },
        "validation": {
            "pooled_marginal": {
                "passed_gates": int(sum(g.passed for g in gates)),
                "total_gates": int(len(gates)),
            },
            "horizon_matched": {
                "horizon": cfg.horizon,
                "historical_blocks": real_stats.n_blocks,
                "independent_years": exceedances[0].independent_years,
                "passed_gates": int(sum(g.passed for g in matched_gates)),
                "total_gates": int(len(matched_gates)),
            },
            "worst_year_exceedance": {
                c.statistic: {
                    "historical_max": c.historical_max,
                    "model_annual_probability": c.synthetic_exceedance_probability,
                    "expected_count_in_sample": c.implied_expected_count,
                    "poisson_interval": [c.lower_count, c.upper_count],
                    "passed": c.passed,
                }
                for c in exceedances
            },
        },
    }
    manifest_path = cfg.output_dir / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    report_path = cfg.output_dir / "validation_report.md"
    write_report(
        report_path,
        summary=diagnostic_summary,
        params=p,
        gates=gates,
        matched_gates=matched_gates,
        exceedances=exceedances,
        real_stats=real_stats,
        synthetic_stats=synthetic_stats,
        leave_out=leave_out,
        real_moments=(
            float(returns.std(ddof=1)),
            float(returns.skew()),
            float(returns.kurtosis()),
        ),
        real_max_abs_return=float(returns.abs().max()),
        synthetic_max_abs_return=float(abs(synthetic).max()),
        stress_episode=stress_episode,
        n_returns=len(returns),
    )

    print(
        f"Pooled marginal gates:  {sum(g.passed for g in gates)}/{len(gates)} passed."
    )
    for g in gates:
        print(f"  {'PASS' if g.passed else 'FAIL'}  {g.metric}")
    print(
        f"Horizon-matched gates:  {sum(g.passed for g in matched_gates)}/"
        f"{len(matched_gates)} passed."
    )
    for g in matched_gates:
        print(f"  {'PASS' if g.passed else 'FAIL'}  {g.metric}")
    print("Worst-observed-year exceedance checks:")
    for c in exceedances:
        print(
            f"  {'PASS' if c.passed else 'FAIL'}  {c.statistic}: "
            f"model p={c.synthetic_exceedance_probability:.3%} per year, "
            f"expected {c.implied_expected_count:.2f} in {c.independent_years} years "
            f"(interval {c.lower_count:.2f}-{c.upper_count:.2f})"
        )
    print(f"Report written to {report_path}")
    print(f"Fit summary written to {fit_summary_path}")
    print(f"Run manifest written to {manifest_path}")


if __name__ == "__main__":
    main()
