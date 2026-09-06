"""Brent Scenario & Risk Lab.

Run with:  streamlit run app.py

The modelling core lives in `src/xtra_takehome`; this file is presentation and
routing only. Every number shown is computed by the same code that produces
`reports/validation_report.md`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent / "src"))

from xtra_takehome.app import charts, llm, risk, services, state  # noqa: E402
from xtra_takehome.app import stress as stress_lab  # noqa: E402

st.set_page_config(
    page_title="Brent Scenario & Risk Lab",
    page_icon="🛢️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      .block-container {padding-top: 2.2rem; max-width: 1400px;}
      h1, h2, h3 {letter-spacing: -0.01em;}
      div[data-testid="stMetricValue"] {font-size: 1.5rem;}
      div[data-testid="stMetric"] {
          background: rgba(128,138,140,0.06);
          border: 1px solid rgba(128,138,140,0.18);
          border-radius: 8px; padding: 12px 14px;
      }
      .stTabs [data-baseweb="tab-list"] {gap: 4px;}
      code {font-size: 0.88em;}
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Cached loaders. Scenario generation is cached on its full argument tuple, so a
# change of seed, horizon, path count or starting state produces a fresh run.
# ---------------------------------------------------------------------------


@st.cache_data(show_spinner="Fetching Brent history…")
def _market():
    return services.load_market_data()


@st.cache_resource(show_spinner="Fitting the scenario generator…")
def _model(returns_hash: str):
    return services.fit_model(_market().returns)


@st.cache_data(show_spinner="Computing historical diagnostics…")
def _diagnostics(returns_hash: str):
    return services.compute_diagnostics(_market().returns)


@st.cache_data(show_spinner="Running the repository validation suite — about 30s the first time…")
def _validation(returns_hash: str):
    return services.run_validation(_model(returns_hash), _market().returns)


@st.cache_data(show_spinner="Generating scenarios…")
def _scenarios(n_paths: int, horizon: int, seed: int, initial_state: str,
               initial_price: float, returns_hash: str):
    return services.generate_scenarios(
        _model(returns_hash), n_paths, horizon, seed, initial_state, initial_price
    )


def _hash(market) -> str:
    return f"{market.n_returns}:{market.latest_date:%Y-%m-%d}:{market.latest_price:.4f}"


market = _market()
key = _hash(market)
model = _model(key)
diagnostics = _diagnostics(key)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

st.sidebar.title("🛢️ Brent Scenario & Risk Lab")
st.sidebar.caption(state.NOT_A_FORECAST)

PAGES = [
    "Overview",
    "Historical data",
    "Model explainer",
    "Model fit & validation",
    "Live scenario lab",
    "Risk analysis",
    "Stress test lab",
    "Ask AI",
]
page = st.sidebar.radio("Page", PAGES, label_visibility="collapsed")

st.sidebar.divider()
st.sidebar.subheader("Scenario settings")
n_paths = st.sidebar.select_slider("Number of scenarios", options=[100, 500, 1000, 5000],
                                   value=1000)
horizon = st.sidebar.select_slider("Horizon (trading days)", options=[30, 60, 90, 126, 252],
                                   value=252)
seed = st.sidebar.number_input("Random seed", min_value=0, max_value=10**6, value=42, step=1)
start_mode = st.sidebar.radio(
    "Starting state",
    ["latest", "historical_mix"],
    format_func=lambda v: "Current market state" if v == "latest" else "Historical mixture",
)
st.sidebar.caption(state.INITIAL_STATE_NOTE[start_mode])

if st.sidebar.button("Generate scenarios", type="primary", use_container_width=True):
    st.session_state["scenarios"] = _scenarios(
        int(n_paths), int(horizon), int(seed), start_mode, market.latest_price, key
    )
    st.session_state["settings_used"] = (int(n_paths), int(horizon), int(seed), start_mode)

scenarios = st.session_state.get("scenarios")
if scenarios is not None:
    used = st.session_state.get("settings_used")
    if used != (int(n_paths), int(horizon), int(seed), start_mode):
        st.sidebar.warning("Settings changed since the last run. Press Generate to refresh.")
    st.sidebar.success(
        f"{scenarios.n_paths:,} paths × {scenarios.horizon}d · "
        f"{scenarios.runtime_seconds:.2f}s"
    )

st.sidebar.divider()
with st.sidebar.expander("Glossary"):
    for term, meaning in state.GLOSSARY.items():
        st.markdown(f"**{term}** — {meaning}")


def require_scenarios():
    if st.session_state.get("scenarios") is None:
        st.info("Press **Generate scenarios** in the sidebar to populate this page.")
        st.stop()
    return st.session_state["scenarios"]


# ---------------------------------------------------------------------------
# Page 1 — Overview
# ---------------------------------------------------------------------------

if page == "Overview":
    st.title("Brent Scenario & Risk Lab")
    st.markdown(
        "A generator of plausible future Brent scenarios, and the machinery to check "
        "whether they can be trusted. Every number here is produced by the same code "
        "that writes the repository's validation report."
    )
    st.warning(state.NOT_A_FORECAST)

    c = st.columns(4)
    c[0].metric("Historical observations", f"{market.n_returns:,}")
    c[1].metric("Period", f"{market.first_date:%Y} – {market.latest_date:%Y}")
    c[2].metric("Latest close", f"${market.latest_price:,.2f}")
    c[3].metric("Non-overlapping years", f"{services.independent_years(market.n_returns)}",
                help="Blocks of 252 days that share no data. An upper bound on the "
                     "number of independent observations about a 'bad year'.")

    st.subheader("The fitted model")
    c = st.columns(4)
    c[0].metric("Specification", "GJR-GARCH(1,1,1)", help="With Hansen skewed-t innovations")
    c[1].metric("Conditional volatility today", f"{model.latest_conditional_volatility:.2f} %/day",
                help="The last fitted conditional volatility — where the market is now.")
    c[2].metric("Effective persistence", f"{model.params.effective_persistence:.4f}",
                help="Close to 1: shocks to volatility decay slowly.")
    c[3].metric("Implied unconditional vol", f"{model.implied_unconditional_volatility:.2f} %/day",
                help="The level the variance recursion drifts toward.")

    c = st.columns(4)
    c[0].metric("Default horizon", f"{services.CANONICAL.horizon} days")
    c[1].metric("Default scenarios", f"{services.CANONICAL.n_paths:,}")
    c[2].metric("Implied tail index", f"{model.params.implied_return_tail_index:.2f}",
                help="Predicted by the fitted dynamics; compare with the Hill estimate.")
    c[3].metric("Hill tail index (loss side)", f"{diagnostics.summary.hill_left:.2f}",
                help="Estimated straight from the returns. It never entered the fit.")

    st.subheader("How a scenario is made")
    for i, (name, detail) in enumerate(state.PIPELINE):
        cols = st.columns([0.5, 3.2, 8])
        cols[0].markdown(f"**{i + 1}**")
        cols[1].markdown(f"**{name}**")
        cols[2].caption(detail)

    with st.expander("An analogy — the state of the sea"):
        st.markdown(state.SEA_ANALOGY)

    st.subheader("What this application is for")
    a, b = st.columns(2)
    a.markdown(
        "**It models**\n\n"
        "- conditional volatility and how it evolves\n"
        "- the distribution of future returns over a horizon\n"
        "- tail behaviour and drawdowns under the fitted model\n"
        "- how often severe years occur, under that model"
    )
    b.markdown(
        "**It does not predict**\n\n"
        "- tomorrow's return or direction\n"
        "- the Brent price at any future date\n"
        "- structural breaks that have never been observed\n"
        "- anything about other assets — the model is univariate"
    )


# ---------------------------------------------------------------------------
# Page 2 — Historical data
# ---------------------------------------------------------------------------

elif page == "Historical data":
    st.title("Historical data")
    st.caption(
        f"Brent `BZ=F` · {market.first_date:%d %b %Y} to {market.latest_date:%d %b %Y} · "
        f"{market.n_returns:,} daily log returns"
    )
    s = diagnostics.summary

    c = st.columns(5)
    c[0].metric("Mean daily return", f"{s.mean:.4f} %")
    c[1].metric("Volatility", f"{s.std:.3f} %/day")
    c[2].metric("Skewness", f"{s.skew:.3f}", help="Negative: losses have the longer tail.")
    c[3].metric("Excess kurtosis", f"{s.excess_kurtosis:.2f}",
                help="Zero for a normal distribution. Extremes are far more frequent here.")
    c[4].metric("Worst day", f"{s.min_return:.2f} %")

    tabs = st.tabs(["Prices & returns", "Distribution", "Autocorrelation", "Tails"])

    with tabs[0]:
        st.plotly_chart(charts.price_history(market.close), use_container_width=True)
        st.caption("Notice there is no persistent trend — over sixteen years the level "
                   "wanders. All the structure worth modelling is in the variation.")
        st.plotly_chart(charts.returns_history(market.returns), use_container_width=True)
        st.caption("Notice the bands of agitation. Large moves are not spread evenly "
                   "through time; they arrive in clusters. That is the single most "
                   "important feature of this series.")
        st.plotly_chart(charts.rolling_volatility(diagnostics.rolling_volatility),
                        use_container_width=True)
        st.caption("The same clustering seen directly: volatility rises and falls in "
                   "sustained regimes rather than jumping around independently.")

    with tabs[1]:
        st.plotly_chart(charts.return_distribution(market.returns), use_container_width=True)
        st.caption(
            f"A tall narrow peak with long tails on both sides. Excess kurtosis is "
            f"{s.excess_kurtosis:.1f}; a normal distribution would give zero. Under a "
            f"normal model with this volatility, the worst observed day "
            f"({s.min_return:.1f}%) would be effectively impossible."
        )
        q = st.columns(4)
        q[0].metric("1% quantile", f"{s.q01:.2f} %")
        q[1].metric("5% quantile", f"{s.q05:.2f} %")
        q[2].metric("95% quantile", f"{s.q95:.2f} %")
        q[3].metric("99% quantile", f"{s.q99:.2f} %")

    with tabs[2]:
        st.plotly_chart(
            charts.acf_comparison(diagnostics.return_acf, diagnostics.squared_acf, s.n),
            use_container_width=True,
        )
        st.caption(
            f"Raw return autocorrelation is weak (largest absolute value "
            f"{s.max_abs_return_acf:.3f}), while squared-return autocorrelation is "
            f"much stronger (mean absolute {s.mean_abs_squared_acf:.3f}). Return "
            f"direction is hard to forecast, but volatility is persistent. This single "
            f"contrast is what rules out an independent-day model and motivates GARCH."
        )

    with tabs[3]:
        a, b = st.columns(2)
        with a:
            st.plotly_chart(
                charts.hill_profile_chart(diagnostics.hill_k, diagnostics.hill_left,
                                          diagnostics.hill_right),
                use_container_width=True,
            )
            st.caption(
                f"The tail index sits near {s.hill_left:.1f} on both sides — between "
                "the α=2 and α=4 boundaries. Variance exists; the fourth moment does "
                "not. That is why sample kurtosis is unstable, which matters on the "
                "validation page."
            )
        with b:
            st.plotly_chart(
                charts.mean_excess_chart(diagnostics.mean_excess_thresholds,
                                         diagnostics.mean_excess_values),
                use_container_width=True,
            )
            st.caption("The mean excess rises with the threshold. When losses get bad "
                       "they get much worse than the threshold suggests — the signature "
                       "of a heavy tail rather than an exponential one.")
        st.info(
            f"The two tail indices are close ({s.hill_left:.2f} on the loss side, "
            f"{s.hill_right:.2f} on the gain side), so the negative skewness of "
            f"{s.skew:.2f} does not come from a slower-decaying loss tail. It comes "
            "from the body of the distribution and from how volatility responds to "
            "negative shocks — which is exactly what the GJR term models."
        )


# ---------------------------------------------------------------------------
# Page 3 — Model explainer
# ---------------------------------------------------------------------------

elif page == "Model explainer":
    st.title("What the model actually says")
    p = model.params

    st.latex(r"r_t = \mu + \varepsilon_t, \qquad \varepsilon_t = \sigma_t z_t")
    st.latex(
        r"\sigma_t^2 = \omega + \alpha\,\varepsilon_{t-1}^2 "
        r"+ \gamma\,\mathbb{1}(\varepsilon_{t-1}<0)\,\varepsilon_{t-1}^2 "
        r"+ \beta\,\sigma_{t-1}^2"
    )
    st.latex(r"z_t \sim \text{standardized skewed-}t(\eta, \lambda)")

    c = st.columns(4)
    c[0].metric("μ", f"{p.mu:.5f}", help="Average daily drift — essentially zero.")
    c[1].metric("ω", f"{p.omega:.4f}", help="The floor the variance never drops below.")
    c[2].metric("α", f"{p.alpha:.4f}", help="How much of yesterday's squared shock carries over.")
    c[3].metric("β", f"{p.beta:.4f}", help="How much of yesterday's variance persists.")
    c = st.columns(4)
    c[0].metric("γ", f"{p.gamma:.4f}", help="The extra variance that only negative shocks add.")
    c[1].metric("η", f"{p.eta:.3f}", help="Tail shape of the innovations. Lower is heavier.")
    c[2].metric("λ", f"{p.lam:.4f}", help="Innovation asymmetry. Negative tilts toward losses.")
    c[3].metric("Effective persistence", f"{p.effective_persistence:.4f}")

    st.subheader("Four things that are easy to confuse")
    t = st.tabs(["Volatility dynamics", "Standardized innovations",
                 "Asymmetry in volatility", "Asymmetry in the distribution"])

    with t[0]:
        st.markdown(
            "The variance recursion has memory. Today's variance is a floor `ω`, plus a "
            "share of yesterday's squared shock, plus a share of yesterday's variance. "
            f"With β = {p.beta:.3f} the memory is long: a shock takes months to fade."
        )
        st.metric("Share of a shock still present after one year",
                  f"{p.effective_persistence ** 252:.1%}")
        st.caption("Effective persistence raised to the power of 252 trading days.")

    with t[1]:
        st.markdown(
            "The standardized innovation is **not** a return:"
        )
        st.latex(r"z_t = \varepsilon_t / \sigma_t")
        st.markdown(
            "It is the shock measured in units of the volatility that was expected that "
            "day. A z of −3 is 'three times the currently expected move'. In a calm "
            f"market where σ is 1%/day that is a −3% day; in a storm where σ is "
            f"{model.latest_conditional_volatility * 2:.1f}%/day the same z is a much "
            "larger move. Reading z as a percentage return is a common and serious error."
        )

    with t[2]:
        st.markdown(
            f"The γ term makes falls feed volatility harder than rises. With "
            f"α = {p.alpha:.4f} and γ = {p.gamma:.4f}, a negative shock contributes "
            f"α + γ = {p.alpha + p.gamma:.4f} while a positive one contributes only α."
        )
        magnitude = st.slider("Shock magnitude (%)", 1.0, 12.0, 3.0, 0.5)
        _, current_var = model.generator.latest_state
        ex = stress_lab.asymmetry_example(p, magnitude, current_var)
        a, b, d = st.columns(3)
        a.metric(f"σ tomorrow after −{magnitude:.1f}%", f"{ex['volatility_after_down']:.4f} %/day")
        b.metric(f"σ tomorrow after +{magnitude:.1f}%", f"{ex['volatility_after_up']:.4f} %/day")
        d.metric("Ratio", f"{ex['ratio']:.4f}×")
        st.caption(
            "Identical magnitudes, different consequences. This is the leverage effect, "
            "and it is what lets the model produce negative skewness in returns even "
            "though it is driven by a nearly symmetric shock process."
        )

    with t[3]:
        st.markdown(
            f"Separately from the volatility response, the innovation distribution itself "
            f"is tilted: λ = {p.lam:.4f}. A negative λ means that even after standardizing "
            "by volatility, the shocks are more likely to be moderately negative than "
            "moderately positive."
        )
        st.info(
            "These are two different mechanisms and both are present. **γ** is asymmetry "
            "in how volatility *reacts* to a shock. **λ** is asymmetry in the shock "
            "*distribution* itself. A model can have either without the other."
        )

    with st.expander("Structural diagnostics implied by the fit"):
        c = st.columns(3)
        c[0].metric("E[A(z)²]", f"{p.fourth_moment_coefficient:.4f}",
                    help="At or above 1 means no finite unconditional fourth moment.")
        c[1].metric("Implied return tail index", f"{p.implied_return_tail_index:.3f}")
        c[2].metric("Hill estimate from data", f"{diagnostics.summary.hill_left:.3f}")
        st.markdown(
            f"The fit implies a tail index of {p.implied_return_tail_index:.2f} for the "
            f"returns. Estimated directly from the data, the Hill estimator gives "
            f"{diagnostics.summary.hill_left:.2f}. They agree to within about "
            f"{abs(p.implied_return_tail_index - diagnostics.summary.hill_left) / diagnostics.summary.hill_left:.0%}, "
            "and the agreement is not circular: the tail index never entered the "
            "likelihood, which only ever saw the conditional density. A volatility model "
            "reproducing an unconditional property it was not fitted to is evidence that "
            "it has captured the mechanism rather than memorized the sample."
        )
        st.warning(
            f"E[A(z)²] = {p.fourth_moment_coefficient:.4f} ≥ 1. "
            + state.EXTRAPOLATION_WARNING
        )


# ---------------------------------------------------------------------------
# Page 4 — Model fit & validation
# ---------------------------------------------------------------------------

elif page == "Model fit & validation":
    st.title("Model fit & validation")
    validation = _validation(key)

    st.info(
        f"Validation always runs at the canonical configuration — "
        f"{services.CANONICAL.horizon}-day horizon, {services.CANONICAL.n_paths:,} paths, "
        f"seed {services.CANONICAL.seed}, historical-mixture start. The acceptance "
        "tolerances were derived for that setup, so it does not follow the sidebar "
        "controls: running a 252-day acceptance test against 30-day scenarios would "
        "produce marks with no meaning."
    )

    c = st.columns(3)
    c[0].metric("Pooled marginal gates",
                f"{validation.pooled_passed} / {len(validation.pooled_gates)}")
    c[1].metric("Horizon-matched gates",
                f"{validation.matched_passed} / {len(validation.matched_gates)}")
    c[2].metric("Extreme-region checks",
                f"{sum(not c_.flagged for c_ in validation.extremes)} / {len(validation.extremes)}",
                help="Plausible rather than flagged.")

    t = st.tabs(["Why two families", "Pooled", "Horizon-matched",
                 "Matched-length", "Extreme region", "Distributions"])

    with t[0]:
        st.markdown(
            "Some statistics depend on how much data you feed them. The pooled family "
            f"compares {services.CANONICAL.n_paths * services.CANONICAL.horizon:,} synthetic "
            f"observations against {market.n_returns:,} historical ones — sixty times more. "
            "For a statistic with a stable population value that is fine. For one that "
            "grows with sample size it measures the sample size, not the model."
        )
        st.markdown(
            "Two statistics here are exactly of that kind. The sample ACF of squared "
            "returns is biased toward zero in short blocks. And because the fitted process "
            "has no finite fourth moment, sample kurtosis has no value to converge to at "
            "all — it simply grows."
        )
        st.success(
            "**The fix is to estimate on equal-length blocks on both sides.** That is the "
            "horizon-matched family. Same tolerances, different estimator."
        )
        st.warning(
            "**A tolerance that travels between estimators must be rescaled.** An audit "
            "found that carrying the absolute ACF tolerance across shrank the target "
            "roughly threefold while the threshold stayed put — an iid bootstrap of the "
            "real returns, with no volatility clustering at all, scored 0.0498 against a "
            "0.05 threshold and passed. The tolerance is now a fraction of the scale under "
            "the estimator in use, and the correction makes this model **fail** that gate "
            "rather than pass it."
        )

    with t[1]:
        rows = [{"Metric": g.metric,
                 "Historical": "n/a" if g.metric == "squared-return ACF MAE" else f"{g.real:.4f}",
                 "Synthetic": f"{g.synthetic:.4f}",
                 "Error": f"{100 * g.error:.1f}%" if g.error_type == "relative" else f"{g.error:.4f}",
                 "Tolerance": f"{100 * g.threshold:.0f}%" if g.error_type == "relative" else f"{g.threshold:.4f}",
                 "Result": state.pass_badge(g.passed)} for g in validation.pooled_gates]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        for d in validation.pooled_diagnostics:
            st.caption(f"**{d.name}** — historical {d.real:.4f}, synthetic {d.synthetic:.4f}. {d.note}")

    with t[2]:
        rows = [{"Metric": g.metric,
                 "Historical median": ("n/a" if g.metric == "squared-return ACF MAE"
                                       else f"{g.real:.4f}"),
                 "Synthetic median": f"{g.synthetic:.4f}",
                 "Error": f"{100 * g.error:.1f}%" if g.error_type == "relative" else f"{g.error:.4f}",
                 "Tolerance": f"{100 * g.threshold:.0f}%" if g.error_type == "relative" else f"{g.threshold:.4f}",
                 "Result": state.pass_badge(g.passed)} for g in validation.matched_gates]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        failures = [g for g in validation.matched_gates if not g.passed]
        if failures:
            g = failures[0]
            st.error(
                f"**The one genuine failure: {g.metric}.** Measured {g.error:.4f} against a "
                f"tolerance of {g.threshold:.4f}. This is not simulation noise — two "
                f"independent runs of this same model differ from each other by only "
                f"{validation.acf_floor_median:.4f} on the identical statistic, so the gap "
                f"with history is about {g.error / validation.acf_floor_median:.0f}× the "
                "irreducible Monte Carlo floor. The model captures volatility clustering "
                "but does not reproduce the detailed memory-decay profile: the historical "
                "ACF decays slowly and irregularly, the model's decays geometrically."
            )
        st.plotly_chart(
            charts.squared_acf_validation(
                validation.extras["real_sq_acf"],
                validation.real_stats.mean_squared_acf,
                validation.real_stats.squared_acf_p05,
                validation.real_stats.squared_acf_p95,
                validation.synthetic_stats.mean_squared_acf,
                services.CANONICAL.horizon,
            ),
            use_container_width=True,
        )

    with t[3]:
        st.markdown(
            "Simulating whole records of the same length as the historical one, then "
            "locating the observed value inside the model's own distribution. This is the "
            "decisive check for the pooled moments."
        )
        rows = [{"Statistic": r.statistic, "Historical": f"{r.historical:.4f}",
                 "Model median": f"{r.model_median:.4f}",
                 "Model 5–95% band": f"[{r.model_p05:.3f}, {r.model_p95:.3f}]",
                 "Percentile of historical": f"{r.percentile:.0f}",
                 "Inside band": "✅ yes" if r.inside else "❌ no"}
                for r in validation.references]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.success(
            "Every historical value falls inside the model's own band for a record of this "
            "length. That does not prove the marginals are correctly calibrated — this is "
            "an in-sample generative check — but it does mean the pooled failures are not, "
            "by themselves, evidence of miscalibration."
        )

    with t[4]:
        st.markdown(
            "Comparing the worst simulated year with the worst observed year directly "
            "would be wrong: the maximum of a heavy-tailed sample grows with the sample, "
            "so that comparison measures the simulation budget. Instead the model is "
            f"projected onto a record of the same length — "
            f"{services.independent_years(market.n_returns)} non-overlapping years."
        )
        rows = [{"Statistic": c_.statistic,
                 "Worst observed year": f"{c_.historical_max:.4f}",
                 "Model annual probability": f"{100 * c_.annual_exceedance_probability:.2f}%",
                 "P(record max ≤ observed)": f"{100 * c_.probability_below:.0f}%",
                 "Verdict": "⚠️ flagged" if c_.flagged else "plausible"}
                for c_ in validation.extremes]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption(
            "A value in the middle of the last column means the observed extreme is a "
            "typical draw for a record of this length. Near 0% would mean the model almost "
            "always produces something worse; near 100%, that it cannot reach what was "
            "observed. None of these is a formal gate — with so few independent years the "
            "data does not support a tight acceptance criterion here."
        )
        st.warning(state.EXTRAPOLATION_WARNING)

    with t[5]:
        st.plotly_chart(
            charts.overlay_distribution(market.returns.to_numpy(),
                                        validation.synthetic_returns.reshape(-1),
                                        "Daily return distribution: historical vs synthetic",
                                        "Return (%)"),
            use_container_width=True,
        )
        st.plotly_chart(
            charts.overlay_distribution(validation.real_stats.max_drawdown,
                                        validation.synthetic_stats.max_drawdown,
                                        "252-day maximum drawdown: historical vs synthetic",
                                        "Maximum drawdown (%)", percent=True),
            use_container_width=True,
        )
        st.caption(
            "Drawdowns are the most demanding comparison because they depend on the order "
            "of the days, not only their distribution. Reproducing them is evidence that "
            "the volatility clustering is working."
        )


# ---------------------------------------------------------------------------
# Page 5 — Live scenario lab
# ---------------------------------------------------------------------------

elif page == "Live scenario lab":
    st.title("Live scenario lab")
    sc = require_scenarios()
    paths = sc.returns

    st.caption(
        f"{sc.n_paths:,} scenarios × {sc.horizon} trading days · seed {sc.seed} · "
        f"start: {sc.initial_state} · generated in {sc.runtime_seconds:.2f}s"
    )
    st.info(state.INITIAL_STATE_NOTE.get(sc.initial_state, ""))

    terminal = risk.terminal_simple_return(paths)
    dd = risk.max_drawdowns(paths)
    c = st.columns(4)
    c[0].metric("Median terminal return", f"{np.median(terminal) * 100:+.1f} %")
    c[1].metric("5th percentile", f"{np.percentile(terminal, 5) * 100:+.1f} %")
    c[2].metric("95th percentile", f"{np.percentile(terminal, 95) * 100:+.1f} %")
    c[3].metric("Median max drawdown", f"{np.median(dd) * 100:.1f} %")

    t = st.tabs(["Fan chart", "Individual paths", "Terminal outcomes",
                 "Drawdowns & volatility", "Best and worst", "Inspect one scenario"])

    with t[0]:
        as_price = st.toggle("Show as price instead of cumulative return", value=False)
        st.plotly_chart(
            charts.fan_chart(paths, sc.initial_price, as_price=as_price),
            use_container_width=True,
        )
        st.caption(
            "Each band holds a fixed share of scenarios. The fan widens with time because "
            "uncertainty accumulates. **The median line is not a forecast** — it is the "
            "middle of a distribution, and no individual scenario is expected to follow it."
        )

    with t[1]:
        n_show = st.slider("Paths to draw", 10, 200, 60, 10)
        as_price = st.toggle("Show as price", value=False, key="paths_price")
        st.plotly_chart(
            charts.sample_paths(paths, n_show, sc.seed, sc.initial_price, as_price=as_price),
            use_container_width=True,
        )
        st.caption(
            f"A random sample of {n_show} out of {sc.n_paths:,}. Drawing every path would "
            "produce an opaque block that hides exactly the structure worth seeing."
        )

    with t[2]:
        a, b = st.columns(2)
        with a:
            st.plotly_chart(
                charts.distribution(terminal, "Terminal cumulative return",
                                    "Total return over the horizon (%)", reference=0.0,
                                    reference_label="unchanged", percent=True),
                use_container_width=True,
            )
        with b:
            st.plotly_chart(
                charts.distribution(risk.terminal_prices(paths, sc.initial_price),
                                    "Terminal price", "Price at horizon (USD)",
                                    reference=sc.initial_price, reference_label="today"),
                use_container_width=True,
            )
        st.caption(
            "Terminal returns are simple returns derived from the summed log returns, so "
            "they can be read directly as gains and losses. Prices are reconstructed from "
            f"the latest close of ${sc.initial_price:,.2f}."
        )

    with t[3]:
        a, b = st.columns(2)
        with a:
            st.plotly_chart(
                charts.distribution(dd, "Maximum drawdown", "Peak-to-trough fall (%)",
                                    percent=True),
                use_container_width=True,
            )
            st.caption("Path-dependent: the deepest fall from a running peak within the horizon.")
        with b:
            st.plotly_chart(
                charts.distribution(risk.realized_volatility(paths),
                                    "Realized volatility within each scenario",
                                    "Annualized volatility (%)"),
                use_container_width=True,
            )
            st.caption("How volatile each simulated year actually turned out, annualized at 252 days.")

    with t[4]:
        n_extreme = st.slider("How many", 3, 25, 8)
        order = np.argsort(terminal)
        worst, best = order[:n_extreme], order[::-1][:n_extreme]
        a, b = st.columns(2)
        a.markdown("**Worst scenarios**")
        a.dataframe(pd.DataFrame({
            "Scenario": [int(i) for i in worst],
            "Terminal return": [f"{terminal[i] * 100:+.1f}%" for i in worst],
            "Max drawdown": [f"{dd[i] * 100:.1f}%" for i in worst],
            "Worst day": [f"{risk.simple_from_log(paths[i].min()):.1f}%" for i in worst],
        }), use_container_width=True, hide_index=True)
        b.markdown("**Best scenarios**")
        b.dataframe(pd.DataFrame({
            "Scenario": [int(i) for i in best],
            "Terminal return": [f"{terminal[i] * 100:+.1f}%" for i in best],
            "Max drawdown": [f"{dd[i] * 100:.1f}%" for i in best],
            "Best day": [f"{risk.simple_from_log(paths[i].max()):.1f}%" for i in best],
        }), use_container_width=True, hide_index=True)
        st.caption(
            "These are the extremes **of this simulation**, not the extremes the model "
            "considers possible. Generating more paths would find worse ones — which is a "
            "property of sampling, not new information about risk. Terminal returns and "
            "single-day figures are shown as simple returns; the model itself works in "
            "log returns, which differ materially once a move is large."
        )

    with t[5]:
        default = int(np.argsort(terminal)[0])
        scenario_id = st.number_input("Scenario ID", 0, sc.n_paths - 1, default, 1,
                                      help="Defaults to the worst scenario in this run.")
        st.plotly_chart(
            charts.scenario_detail(paths[scenario_id], sc.conditional_volatility[scenario_id],
                                   sc.initial_price, int(scenario_id)),
            use_container_width=True,
        )
        c = st.columns(4)
        c[0].metric("Terminal return", f"{terminal[scenario_id] * 100:+.1f} %")
        c[1].metric("Max drawdown", f"{dd[scenario_id] * 100:.1f} %")
        c[2].metric("Worst day", f"{risk.simple_from_log(paths[scenario_id].min()):.2f} %",
                    help="Simple return, converted from the model's log return.")
        c[3].metric("Peak volatility", f"{sc.conditional_volatility[scenario_id].max():.2f} %/day")


# ---------------------------------------------------------------------------
# Page 6 — Risk analysis
# ---------------------------------------------------------------------------

elif page == "Risk analysis":
    st.title("Risk analysis")
    sc = require_scenarios()
    paths = sc.returns

    st.caption(f"Computed from {sc.n_paths:,} scenarios over {sc.horizon} trading days.")
    st.warning(
        "**Every metric below is labelled with what it is measured over.** A daily VaR and "
        "a horizon VaR answer different questions and must never be compared with each "
        "other or quoted interchangeably."
    )

    threshold_pct = st.slider("Custom loss threshold (%)", 5, 60, 25, 5)
    dd_threshold_pct = st.slider("Custom drawdown threshold (%)", 5, 80, 30, 5)

    thresholds = tuple(sorted({0.10, 0.20, 0.30, threshold_pct / 100.0}))
    metrics = risk.risk_report(paths, sc.initial_price, loss_thresholds=thresholds,
                               drawdown_threshold=dd_threshold_pct / 100.0)

    headings = {
        risk.MetricKind.DAILY: (
            "Daily metrics",
            "Estimated from individual simulated days, pooled across paths. These describe "
            "a single bad day.",
        ),
        risk.MetricKind.TERMINAL: (
            f"Whole-horizon metrics ({sc.horizon} trading days)",
            "One observation per scenario: where the path ends. These are much larger than "
            "the daily figures above because they accumulate over the whole horizon — they "
            "are not comparable with them.",
        ),
        risk.MetricKind.PATH: (
            "Path-dependent metrics",
            "Depend on the whole trajectory, not just the endpoint. Two scenarios can end "
            "in the same place having taken very different routes.",
        ),
    }
    for kind in risk.MetricKind:
        subset = [m for m in metrics if m.kind is kind]
        if not subset:
            continue
        title, caption = headings[kind]
        st.subheader(title)
        st.caption(caption)
        cols = st.columns(min(4, len(subset)))
        for i, m in enumerate(subset):
            col = cols[i % len(cols)]
            suffix = "%" if "%" in m.unit or m.unit.startswith("%") else ""
            col.metric(m.name, f"{m.value:,.2f}{suffix}", help=m.explanation)

    st.subheader("Terminal return percentiles")
    pct = risk.terminal_return_percentiles(paths)
    st.dataframe(pd.DataFrame({
        "Percentile": [f"{q}%" for q in pct],
        "Terminal return": [f"{v * 100:+.2f}%" for v in pct.values()],
        "Terminal price": [f"${sc.initial_price * (1 + v):,.2f}" for v in pct.values()],
    }), use_container_width=True, hide_index=True)

    st.subheader("Monte Carlo convergence")
    st.markdown(state.MONTE_CARLO_VS_MODEL_RISK)
    if st.button("Run convergence study", help="Re-simulates at increasing path counts."):
        counts = (100, 500, 1000, 5000, 10000)
        with st.spinner("Simulating at increasing path counts…"):
            points = risk.convergence_curve(model.generator, sc.horizon, sc.seed, counts,
                                            sc.initial_state)
        st.session_state["convergence"] = points

    points = st.session_state.get("convergence")
    if points:
        cols = st.columns(2)
        for i, (metric, label) in enumerate([("var95", "Daily VaR 95%"),
                                             ("var99", "Daily VaR 99%"),
                                             ("es95", "Daily ES 95%"),
                                             ("drawdown_p95", "Drawdown p95 (%)")]):
            cols[i % 2].plotly_chart(charts.convergence_chart(points, metric, label),
                                     use_container_width=True)
        st.dataframe(pd.DataFrame([{
            "Paths": f"{p.n_paths:,}", "VaR 95%": f"{p.var95:.3f}",
            "VaR 99%": f"{p.var99:.3f}", "ES 95%": f"{p.es95:.3f}",
            "Drawdown p95": f"{p.drawdown_p95:.2f}",
        } for p in points]), use_container_width=True, hide_index=True)
        st.error(
            "**The curves flatten. That is simulation noise falling, and nothing else.** "
            "The model risk — whether GJR-skewed-t is the right description of Brent at "
            "all — is unchanged by the number of paths. A wrong model converges just as "
            "smoothly as a right one."
        )


# ---------------------------------------------------------------------------
# Page 7 — Stress test lab
# ---------------------------------------------------------------------------

elif page == "Stress test lab":
    st.title("Stress test lab")
    st.markdown(
        "Two modes, kept apart because they answer different questions and only one of "
        "them carries a probability."
    )

    mode = st.radio("Mode", ["Model-generated stress", "Assumed shock experiment"],
                    horizontal=True)

    if mode == "Model-generated stress":
        sc = require_scenarios()
        paths = sc.returns
        st.success(
            "These are draws from the fitted model. The share of scenarios selected **is** "
            "the model's own estimate of how often such a year occurs."
        )
        criterion = st.selectbox("Severity filter", list(stress_lab.FILTERS))
        idx = stress_lab.filter_scenarios(paths, criterion)
        subset = paths[idx]

        terminal = risk.terminal_simple_return(subset)
        dd = risk.max_drawdowns(subset)
        c = st.columns(4)
        c[0].metric("Scenarios selected", f"{len(idx):,}",
                    help=f"out of {sc.n_paths:,}")
        c[1].metric("Median terminal return", f"{np.median(terminal) * 100:+.1f} %")
        c[2].metric("Median max drawdown", f"{np.median(dd) * 100:.1f} %")
        c[3].metric("Worst day in subset",
                    f"{risk.simple_from_log(subset.min()):.1f} %",
                    help="Simple return. The model works in log returns, which diverge "
                         "from simple returns in the tail; nothing can fall more than 100%.")

        st.plotly_chart(
            charts.sample_paths(paths, 60, sc.seed, sc.initial_price, highlight=idx),
            use_container_width=True,
        )
        st.caption("Stressed scenarios in red against a sample of the full set.")

        a, b = st.columns(2)
        a.plotly_chart(charts.overlay_distribution(
            risk.terminal_simple_return(paths), terminal,
            "Terminal return: all scenarios vs stressed subset", "Return (%)",
            percent=True, reference_label="all scenarios",
            comparison_label="stressed subset"),
            use_container_width=True)
        b.plotly_chart(charts.overlay_distribution(
            risk.max_drawdowns(paths), dd,
            "Max drawdown: all scenarios vs stressed subset", "Drawdown (%)",
            percent=True, reference_label="all scenarios",
            comparison_label="stressed subset"),
            use_container_width=True)

    else:
        st.warning(
            "**These are conditional experiments, not simulation draws and not forecasts.** "
            "You choose the shock sequence, so no probability attaches to it. What the "
            "model supplies is the mechanical consequence: given those returns, this is "
            "what the fitted volatility recursion does next."
        )
        preset = st.selectbox("Preset", ["custom"] + list(stress_lab.PRESETS))
        if preset == "custom":
            raw = st.text_input("Daily shocks, comma separated (%)", "-8, -5, 4, -6, -3")
            try:
                shocks = [float(v.strip()) for v in raw.split(",") if v.strip()]
            except ValueError:
                st.error("Could not read that. Use numbers separated by commas, e.g. `-8, -5, 4`.")
                st.stop()
            if not shocks:
                st.error("Enter at least one shock.")
                st.stop()
        else:
            shocks = stress_lab.PRESETS[preset]
            st.caption(f"Imposed sequence: {', '.join(f'{v:+.1f}%' for v in shocks)}")

        start_from = st.radio("Starting volatility state", ["current market state", "long-run level"],
                              horizontal=True)
        residual, variance = model.generator.latest_state
        if start_from == "long-run level":
            variance = model.params.implied_unconditional_variance
            residual = 0.0
        days_after = st.slider("Days of decay to show", 0, 252, 90, 10)

        experiment = stress_lab.propagate_shocks(model.params, shocks, variance, residual,
                                                 days_after=days_after)
        c = st.columns(4)
        c[0].metric("Volatility before", f"{experiment.starting_volatility:.2f} %/day")
        c[1].metric("Peak volatility", f"{experiment.peak_volatility:.2f} %/day")
        c[2].metric("Multiple", f"{experiment.volatility_multiple:.2f}×")
        c[3].metric("Cumulative shock",
                    f"{(np.expm1(np.sum(experiment.shocks_pct) / 100.0)) * 100:+.1f} %")

        st.plotly_chart(charts.stress_volatility(experiment, days_after), use_container_width=True)

        if experiment.days_to_half_decay is not None:
            st.caption(
                f"Volatility falls halfway back toward its starting level after about "
                f"**{experiment.days_to_half_decay} trading days** — a direct consequence "
                f"of persistence {model.params.effective_persistence:.4f}."
            )
        else:
            st.caption(
                f"Volatility does not return halfway to its starting level within the "
                f"window shown. With persistence {model.params.effective_persistence:.4f} "
                f"the recursion decays toward an implied long-run level of "
                f"{model.implied_unconditional_volatility:.2f} %/day, which is **above** "
                f"the {experiment.starting_volatility:.2f} %/day it started from — so it "
                "settles higher rather than returning."
            )
        st.info(experiment.interpretation)


# ---------------------------------------------------------------------------
# Page 8 — Ask AI
# ---------------------------------------------------------------------------

elif page == "Ask AI":
    st.title("Ask about these results")
    st.caption(
        "Optional. Python computes every number; the assistant only explains and locates "
        "figures that are already in the context. It is instructed never to calculate."
    )

    if not llm.api_key_available():
        st.info(
            f"**AI analysis is disabled.** Set the `{llm.ENV_VAR}` environment variable and "
            "restart to enable this page. Everything else in the application works without it."
        )
        with st.expander("What the assistant would be given"):
            st.markdown(
                "- fitted model parameters and structural diagnostics\n"
                "- historical diagnostics\n"
                "- the full validation results, including failures\n"
                "- current scenario settings and computed risk metrics\n"
                "- stress results and any selected scenario"
            )
            st.code(llm.SYSTEM_PROMPT, language="text")
        st.stop()

    validation = _validation(key)
    sc = st.session_state.get("scenarios")
    metrics_payload = None
    if sc is not None:
        metrics_payload = llm.metrics_payload(risk.risk_report(sc.returns, sc.initial_price))

    context = llm.build_context(model, diagnostics, validation, scenarios=sc,
                                risk_metrics=metrics_payload)
    st.caption(f"Context sections available: {', '.join(context.available_sections)}")
    if sc is None:
        st.info("No scenarios generated yet, so scenario and risk questions cannot be "
                "answered. Generate scenarios in the sidebar to widen the context.")

    st.markdown("**Suggested questions**")
    cols = st.columns(2)
    for i, q in enumerate(llm.SUGGESTED_QUESTIONS):
        if cols[i % 2].button(q, use_container_width=True, key=f"sq{i}"):
            st.session_state["question"] = q

    question = st.text_area("Your question", value=st.session_state.get("question", ""),
                            height=90)
    if st.button("Ask", type="primary") and question.strip():
        with st.spinner("Thinking…"):
            try:
                st.markdown(llm.ask(question, context))
            except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
                st.error(f"Could not get an answer: {exc}")

    with st.expander("Inspect the exact context supplied"):
        st.code(context.to_json(), language="json")
