from __future__ import annotations

import json

import numpy as np

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
    acf_monte_carlo_floor,
    beyond_historical_max_fraction,
    extreme_region_checks,
    leave_out_sensitivity,
    matched_sample_reference,
    pooled_context,
    validate,
    validate_horizon_matched,
)
from .windows import (
    compute_window_stats,
    non_overlapping_block_count,
    non_overlapping_blocks,
    rolling_blocks,
    stress_episode_span,
)


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
            "implied_return_tail_index": p.implied_return_tail_index,
        },
    )

    synthetic = generator.simulate(
        n_steps=cfg.horizon, n_paths=cfg.n_paths, seed=cfg.seed
    )

    # Family 1: pooled marginal.
    gates, diagnostics, extras = validate(
        returns, synthetic, horizon=cfg.horizon, acf_lags=cfg.max_acf_lag
    )

    # Family 2: every statistic estimated inside equal-length blocks.
    print(f"Estimating year-level statistics on {cfg.horizon}-day blocks...")
    returns_array = returns.to_numpy()
    real_stats = compute_window_stats(
        rolling_blocks(returns_array, cfg.horizon), acf_lags=cfg.max_acf_lag
    )
    synthetic_stats = compute_window_stats(synthetic, acf_lags=cfg.max_acf_lag)
    mean_standard_error = pooled_context(
        returns_array, acf_lags=cfg.max_acf_lag
    ).mean_standard_error
    matched_gates = validate_horizon_matched(
        real_stats, synthetic_stats, mean_standard_error
    )

    # Family 3: the stressed region, against a record of the same length.
    disjoint_blocks = non_overlapping_blocks(returns_array, cfg.horizon)
    disjoint_stats = compute_window_stats(disjoint_blocks, acf_lags=cfg.max_acf_lag)
    extremes = extreme_region_checks(disjoint_stats, synthetic_stats)

    print("Simulating records of the historical length for the matched-n reference...")
    references = matched_sample_reference(
        generator, returns_array, cfg.horizon, cfg.n_paths
    )
    acf_floor_median, acf_floor_max = acf_monte_carlo_floor(
        generator, cfg.horizon, cfg.n_paths, cfg.max_acf_lag
    )

    leave_out = leave_out_sensitivity(
        "synthetic", synthetic, synthetic_stats.volatility, drops=(0, 1)
    ) + leave_out_sensitivity(
        "historical", disjoint_blocks, disjoint_stats.volatility, drops=(0, 1)
    )
    beyond_max = beyond_historical_max_fraction(real_stats, synthetic_stats)
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
            "non_overlapping_blocks": non_overlapping_block_count(
                len(returns), cfg.horizon
            ),
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
                "implied_return_tail_index": p.implied_return_tail_index,
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
                "passed_gates": int(sum(g.passed for g in matched_gates)),
                "total_gates": int(len(matched_gates)),
            },
            "squared_acf_monte_carlo_floor": {
                "median": acf_floor_median,
                "max": acf_floor_max,
            },
            "matched_sample_reference": {
                ref.statistic: {
                    "historical": ref.historical,
                    "model_median": ref.model_median,
                    "model_p05": ref.model_p05,
                    "model_p95": ref.model_p95,
                    "percentile": ref.percentile,
                    "inside_90pct_band": ref.inside,
                }
                for ref in references
            },
            "extreme_region": {
                c.statistic: {
                    "historical_max_non_overlapping": c.historical_max,
                    "model_annual_exceedance_probability": c.annual_exceedance_probability,
                    "blocks_in_record": c.n_blocks,
                    "probability_record_contains_one": c.probability_at_least_one,
                    "probability_model_record_max_below_observed": c.probability_below,
                    "flagged": c.flagged,
                }
                for c in extremes
            },
            "beyond_historical_max_year_fraction": beyond_max,
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
        diagnostics=diagnostics,
        matched_gates=matched_gates,
        extremes=extremes,
        references=references,
        real_stats=real_stats,
        synthetic_stats=synthetic_stats,
        leave_out=leave_out,
        beyond_max_fraction=beyond_max,
        acf_floor=(acf_floor_median, acf_floor_max),
        acf_scale=(
            float(extras["acf_scale"]),
            float(np.mean(np.abs(real_stats.mean_squared_acf[1:]))),
        ),
        stress_episode=stress_episode,
        n_returns=len(returns),
    )

    print(f"Pooled marginal gates: {sum(g.passed for g in gates)}/{len(gates)} passed.")
    for g in gates:
        print(f"  {'PASS' if g.passed else 'FAIL'}  {g.metric}")
    print(
        f"Horizon-matched gates: {sum(g.passed for g in matched_gates)}/"
        f"{len(matched_gates)} passed."
    )
    for g in matched_gates:
        print(f"  {'PASS' if g.passed else 'FAIL'}  {g.metric}")
    print("Matched-sample reference (is the observed value a plausible draw?):")
    for ref in references:
        print(
            f"  {'OK  ' if ref.inside else 'FLAG'}  {ref.statistic}: historical "
            f"{ref.historical:.3f} at the {ref.percentile:.1f}th percentile of the "
            f"model's own matched-length distribution"
        )
    print("Extreme region (worst year in a record of the same length):")
    for c in extremes:
        print(
            f"  {'FLAG' if c.flagged else 'OK  '}  {c.statistic}: model annual "
            f"p={c.annual_exceedance_probability:.2%}, "
            f"P(record max <= observed)={c.probability_below:.1%}"
        )
    print(f"Report written to {report_path}")
    print(f"Fit summary written to {fit_summary_path}")
    print(f"Run manifest written to {manifest_path}")


if __name__ == "__main__":
    main()
