"""Run from this directory: streamlit run app.py"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import sys

# Local imports work both with a clean install and with the parent's environment.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))

import numpy as np
import pandas as pd
import streamlit as st
from arch.univariate import SkewStudent
from scipy.stats import norm

from lab import charts as ch, llm, risk, services as svc, stress
from lab.state import commit_run

st.set_page_config(page_title="Brent Scenario & Risk Lab", page_icon="◈", layout="wide")

PAGES = ["01  Overview", "02  Historical Data", "03  Model Explainer", "04  Fit & Validation",
         "05  Live Scenario Lab", "06  Risk Analysis", "07  Stress Test Lab", "08  Ask AI"]

CSS = """<style>
.block-container {padding-top:3.7rem; max-width:1480px; padding-bottom:4rem}
[data-testid="stSidebar"] {border-right:1px solid #D7E1E8}
h1 {font-size:2.6rem!important; letter-spacing:-1.3px; font-weight:650!important}
h2 {font-size:1.6rem!important; letter-spacing:-.4px}
h3 {font-size:1.1rem!important}
[data-testid="stMetric"] {background:white; border:1px solid #DCE5EC; padding:16px 18px; border-radius:10px}
[data-testid="stMetricValue"] {font-size:1.65rem!important; font-variant-numeric:tabular-nums}
[data-testid="stMetricLabel"] {font-size:.85rem!important}
.hero {padding:30px 34px; border-radius:14px; background:#152F43; color:#F8FCFF; margin:8px 0 24px}
.hero h2 {color:#FFFFFF; font-size:2.1rem!important; margin:3px 0 8px}
.hero p {color:#D4E1E9; max-width:820px; margin:0}
.eyebrow {font-size:.72rem; letter-spacing:2px; text-transform:uppercase; color:#73CBD1; font-weight:700}
.step {border-top:3px solid #087F8C; background:white; padding:15px; border-radius:6px; min-height:117px}
.step small {color:#087F8C; font-weight:700}
.step strong {display:block; margin:6px 0; font-size:1rem}
.step span {font-size:.86rem; color:#556C7C}
.note {padding:13px 18px; background:#EBF4F5; border-left:3px solid #087F8C; border-radius:4px; margin:12px 0 20px}
</style>"""
st.markdown(CSS, unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def cached_data(code):
    return svc.load_data()


@st.cache_resource(show_spinner=False)
def cached_fit(returns, code):
    return svc.fit(returns)


@st.cache_data(show_spinner=False)
def cached_diagnostics(returns, code):
    return svc.diagnostics(returns)


@st.cache_data(show_spinner=False)
def cached_validation(returns, parameters, code, _generator):
    return svc.validate(_generator, returns)


@st.cache_data(show_spinner=False)
def model_details(parameters):
    from xtra_takehome.challenger import GjrSkewTParams
    p = GjrSkewTParams(**parameters)
    return dict(persistence=p.effective_persistence, fourth=p.fourth_moment_coefficient,
                tail_index=p.implied_return_tail_index,
                unconditional_volatility=np.sqrt(p.implied_unconditional_variance))


def plot(fig, explanation, key=None):
    st.plotly_chart(fig, use_container_width=True, key=key, config={"displaylogo": False})
    st.caption(explanation)


def cards(items):
    for col, (label, value, help_) in zip(st.columns(len(items)), items):
        col.metric(label, value, help=help_)


def need_run():
    run = st.session_state.get("run")
    if run is None:
        st.info("Choose settings in the sidebar and select GENERATE SCENARIOS to start.")
        return None
    s = run.settings
    st.caption(f"ACTIVE RUN · {s['n_paths']:,} paths × {s['horizon']} trading days · seed {s['seed']} · "
               f"{s['initial_state']} · as of {s['as_of']} · generated in {run.elapsed:.2f}s")
    return run


def gate_table(gates, pooled=False):
    rows = []
    for g in gates:
        unstable = pooled and g.metric in ("skewness", "excess kurtosis")
        rows.append({"Status": "PASS" if g.passed else "FAIL", "Metric": g.metric,
                     "Historical": "—" if g.metric == "squared-return ACF MAE" else f"{g.real:.5g}",
                     "Synthetic": "See multi-seed estimates below" if unstable else f"{g.synthetic:.5g}",
                     "Error": "Unstable single-seed value" if unstable else
                         (f"{g.error:.2%}" if g.error_type == "relative" else f"{g.error:.5f}"),
                     "Tolerance": f"{g.threshold:.2%}" if g.error_type == "relative" else f"{g.threshold:.5f}",
                     "Error type": g.error_type})
    frame = pd.DataFrame(rows)
    def paint(value):
        return "background-color:#FCE8E7;color:#9D2F2F;font-weight:bold" if value == "FAIL" else "background-color:#E3F2EB;color:#22694B;font-weight:bold"
    st.dataframe(frame.style.map(paint, subset=["Status"]), hide_index=True, use_container_width=True,
                 height=min(580, 38+35*len(frame)))


def overview(close, returns, g, d, details):
    st.markdown('<div class="hero"><div class="eyebrow">4-XTRA / QUANTITATIVE RESEARCH</div>'
                '<h2>Explore many possible futures.</h2><p>Individual returns are difficult to predict. '
                'Volatility has temporal structure. Learn how those two facts become a scenario engine '
                'and explore the risk inside its distribution.</p></div>', unsafe_allow_html=True)
    cards([("Historical returns", f"{len(returns):,}", "Daily percentage log returns, without winsorizing extremes."),
           ("Last observed close", f"${close.iloc[-1]:.2f}", "Front-month Brent proxy, USD per barrel."),
           ("Fitted σ at cut-off", f"{np.sqrt(svc.fitted_states(g)[1][-1]):.2f}%", "Conditional daily volatility on the last observed day."),
           ("Default experiment", "1,000 × 252d", "Independent paths, each one trading year.")])
    st.caption(f"Historical price period: {close.index[0].date()} → {close.index[-1].date()} · {svc.MODEL_NAME}")
    st.markdown('<div class="note">The model does not predict the exact future Brent price. It models '
                'the conditional distribution and volatility dynamics to generate plausible scenarios for risk analysis.</div>', unsafe_allow_html=True)
    st.subheader("From market history to a distribution of futures")
    steps = [("01", "Historical Brent → log returns", "Measure proportional daily price changes."),
             ("02", "Diagnostics → fitted generator", "Learn persistence, asymmetry and tails."),
             ("03", "Current state × random innovation", "Scale relative shocks by conditional volatility."),
             ("04", "Recursive paths → risk metrics", "Update variance each day, then compare many futures.")]
    for col, (number, title, text) in zip(st.columns(4), steps):
        col.markdown(f'<div class="step"><small>{number}</small><strong>{title}</strong><span>{text}</span></div>', unsafe_allow_html=True)
    left, right = st.columns([1.8, 1])
    with left:
        plot(ch.lines(returns.index, {"21-day rolling volatility": d["rolling_vol"]},
                      "Volatility moves in clusters", "Date", "Realized volatility (% annualized)"),
             "Calm and stressed periods persist. The model learns a statistical volatility response, not the causes of each episode.")
    with right:
        st.subheader("The state of the sea")
        st.write("GJR-GARCH describes the sea state: conditional volatility σ. The standardized innovation z is a relative wave. "
                 "The effective return shock ε = σz combines the two.")
        st.latex(r"r_t=\mu+\underbrace{\sigma_t}_{\text{sea state}}\underbrace{z_t}_{\text{relative wave}}")
        st.write("A wave of z = −3 means three conditional standard deviations. Its percentage impact depends on σ.")
        st.button("Open Live Scenario Lab →", on_click=lambda: st.session_state.update(page=PAGES[4]), use_container_width=True)
    cards([("α · shock response", f"{g.params_.alpha:.4f}", "Response to squared residuals."),
           ("γ · negative-shock term", f"{g.params_.gamma:.4f}", "Extra variance contribution from negative residuals."),
           ("β · variance memory", f"{g.params_.beta:.4f}", "Lagged conditional variance coefficient."),
           ("Effective persistence", f"{details['persistence']:.4f}", "Uses the exact skew-t lower partial second moment.")])


def history(close, returns, g, d, details):
    st.title("Historical Data")
    st.write("A fixed historical window anchors every fit. BZ=F is a continuous/front-month proxy; contract rolls and series construction can affect returns.")
    tabs = st.tabs(["Prices & returns", "Volatility & dependence", "Tails & quantiles"])
    with tabs[0]:
        plot(ch.lines(close.index, {"Brent close": close}, "Brent close price", "Date", "USD / barrel"),
             "These are observed closes. The laboratory's price paths are reconstructions of this proxy, not a traded futures total-return strategy.")
        plot(ch.lines(returns.index, {"Daily log return": returns}, "Daily percentage log returns", "Date", "100 × log(Pₜ / Pₜ₋₁)"),
             "Extreme observations are retained. A −10% log return corresponds to exp(−0.10) − 1 as a simple return.")
        plot(ch.histogram({"Historical": returns}, "Historical return distribution", "Daily log return (%)", log_y=True),
             "All observations are included. The logarithmic probability axis makes rare tail bins visible.")
    with tabs[1]:
        plot(ch.lines(returns.index, {"21-day sample volatility": d["rolling_vol"]}, "Rolling volatility", "Date", "% annualized"),
             "A 21-day sample standard deviation, annualized by √252. This is realized volatility, not the fitted conditional σ.")
        plot(ch.lines(np.arange(1, 21), {"Returns": d["acf"][1:], "Squared returns": d["squared_acf"][1:]},
                      "Direction versus magnitude dependence", "Lag (trading days)", "Autocorrelation"),
             "Raw return autocorrelation is weak, while squared-return autocorrelation is stronger. Direction is difficult to forecast; volatility persists.")
    with tabs[2]:
        a, b = st.columns(2)
        with a:
            ks, left, right = d["hill"]
            plot(ch.lines(ks, {"Loss tail": left, "Gain tail": right}, "Hill tail exponent", "Largest k observations", "Estimated tail index"),
                 "Smaller exponents mean heavier tails. Estimates vary with k and do not prove which population moments exist.")
        with b:
            thresholds, values = d["excess"]
            plot(ch.lines(thresholds, {"Loss mean excess": values}, "Mean excess over loss threshold", "Loss threshold (% log loss)", "Mean excess (% log loss)"),
                 "An increasing curve is consistent with heavy losses. Very high thresholds use few observations.")
        st.dataframe(pd.DataFrame([{"Quantile": k, "Daily log return (%)": d["summary"][k]} for k in ("q01", "q05", "q95", "q99")]), hide_index=True)


def explainer(close, returns, g, d, details):
    st.title("Model Explainer")
    st.write(svc.MODEL_NAME)
    st.latex(r"r_t=\mu+\epsilon_t,\qquad \epsilon_t=\sigma_t z_t,\qquad z_t\sim\mathrm{Hansen\ skewed\text{-}t}(\eta,\lambda)")
    st.latex(r"\sigma_t^2=\omega+\alpha\epsilon_{t-1}^2+\gamma\mathbf{1}_{\{\epsilon_{t-1}<0\}}\epsilon_{t-1}^2+\beta\sigma_{t-1}^2")
    tab1, tab2, tab3 = st.tabs(["Volatility dynamics", "Relative innovations", "Parameters & limitations"])
    p = g.params_
    with tab1:
        st.write("ω sets the variance intercept; α responds to squared residuals; β carries past conditional variance. "
                 "γ changes the response to negative residuals. It describes volatility asymmetry, separately from the innovation distribution's skew.")
        mag = st.slider("Equal residual-shock magnitude (percentage log-return points)", .1, 15., 3., .1)
        variance = svc.fitted_states(g)[1][-1]
        positive = float(svc.variance_step(p, mag, variance))
        negative = float(svc.variance_step(p, -mag, variance))
        cards([("σ after +ε", f"{np.sqrt(positive):.3f}%", "Positive residual, not standardized z."),
               ("σ after −ε", f"{np.sqrt(negative):.3f}%", "Negative residual of the same magnitude."),
               ("Extra variance γ ε²", f"{negative-positive:.4f}", "Variance units are squared percentage log-return points.")])
        shocks = np.linspace(-15, 15, 151)
        plot(ch.lines(shocks, {"Next-day variance": svc.variance_step(p, shocks, variance)},
                      "News impact curve", "Residual ε (% log-return points)", "Next-day variance (%²)"),
             "The comparison holds the previous variance fixed. Residuals are r − μ; equal residuals differ slightly from equal raw returns.")
    with tab2:
        st.latex(r"z_t=\epsilon_t/\sigma_t")
        sigma = st.slider("Conditional σ (% per day)", .2, 10., 2., .1)
        z = st.slider("Relative innovation z (standard deviations)", -5., 5., -3., .1)
        cards([("Effective shock ε = σz", f"{sigma*z:+.2f}%", "Percentage log-return points."),
               ("Return r = μ + ε", f"{p.mu+sigma*z:+.3f}%", "Percentage log return; not simple percentage price change.")])
        grid = np.linspace(-6, 6, 501)
        dist = SkewStudent()
        density = np.exp(dist.loglikelihood(np.array([p.eta, p.lam]), grid, np.ones_like(grid), individual=True))
        plot(ch.lines(grid, {"Fitted standardized skew-t": density, "Standard normal reference": norm.pdf(grid)},
                      "Innovation distribution", "z (unitless)", "Probability density"),
             "η controls innovation tail shape; λ controls innovation skew. Both densities shown on −6 ≤ z ≤ 6; probability extends beyond this view.")
        st.write("Innovation asymmetry describes which relative shocks occur. GJR asymmetry describes how an observed negative shock changes subsequent variance. These are distinct mechanisms.")
    with tab3:
        st.dataframe(pd.DataFrame([{"Parameter": k, "Estimate": v} for k, v in asdict(p).items()]), hide_index=True)
        st.latex(r"\rho=\alpha+\beta+\gamma E[z^2\mathbf{1}_{z<0}]")
        st.write(f"Exact effective persistence: {details['persistence']:.6f}. Fourth-moment coefficient E[A²]: {details['fourth']:.6f}.")
        if details["fourth"] >= 1:
            st.warning("The fitted stationary law has no finite fourth moment. Stable-looking Monte Carlo percentiles do not validate far-tail extrapolation.")
        st.write(f"Model-implied return tail index: {details['tail_index']:.3f}. This is a fitted-model implication; the Hill curve is a noisy empirical comparator from the same fitting sample.")
        with st.expander("Optimizer summary"):
            st.code(g.fit_summary_)


def validation_page(close, returns, g, d, details):
    st.title("Model Fit & Validation")
    st.caption("CANONICAL CALIBRATION · 1,000 paths × 252 days · seed 42 · historical fitted-state mixture")
    st.write("These checks always use the original validation machinery. Live scenario controls do not change the gates. "
             "PASS/FAIL values are engineering tolerances, not significance tests or evidence of out-of-sample skill.")
    with st.spinner("Running the canonical gates, whole-record reference and 10-seed moment checks…"):
        v = cached_validation(returns, asdict(g.params_), svc.cache_fingerprint(), g)
    st.session_state["validation"] = v
    acf_gate = next(x for x in v["matched"] if x.metric == "squared-return ACF MAE")
    (st.error if not acf_gate.passed else st.success)(
        f"{'FAIL' if not acf_gate.passed else 'PASS'} · Horizon-matched squared-return ACF: "
        f"MAE {acf_gate.error:.5f}, tolerance {acf_gate.threshold:.5f}.")
    st.write("The model captures volatility clustering but does not perfectly reproduce the detailed memory-decay profile. "
             "A single fixed volatility recursion produces a smoother memory profile than the irregular historical block estimates.")
    st.caption(f"Model-versus-model Monte Carlo ACF difference: median {v['acf_floor'][0]:.5f}. "
               "This measures simulation noise at this budget, not historical sampling uncertainty; it is not a formal rejection test.")
    plot(ch.acf_validation(v, d["squared_acf"]),
         "Compare the two 252-day curves. The full-record historical curve uses a different estimator and is context only. ACF is computed separately inside each path.")
    tabs = st.tabs(["Horizon-matched gates", "Pooled gates", "Matched-length reference", "Stress region", "Distributions"])
    with tabs[0]:
        st.write(f"{v['real'].n_blocks:,} overlapping historical windows vs {v['synthetic'].n_blocks:,} synthetic paths. "
                 "Statistics are computed within 252-day blocks and compared at the median; ACF curves use the mean.")
        gate_table(v["matched"])
        st.caption("Scale-dependent tolerances are derived using the estimator's historical scale. The ACF tolerance correction documented in the repository makes the submitted model fail rather than pass.")
        bands = [dict(metric=x.metric, historical_5_95=x.real_band, synthetic_5_95=x.synthetic_band)
                 for x in v["matched"] if x.real_band is not None]
        with st.expander("Block dispersion (descriptive, not acceptance bands)"):
            st.dataframe(pd.DataFrame(bands), hide_index=True, use_container_width=True)
    with tabs[1]:
        st.warning("Pooled sample sizes differ. Skewness, kurtosis and squared-return ACF failures cannot by themselves establish model failure; use the matched estimators alongside them.")
        gate_table(v["pooled"], pooled=True)
        st.caption("Original seed-42 verdicts are retained. Single-seed pooled skewness and kurtosis values are not quoted; multi-seed medians and ranges are below, without re-gating those medians.")
        st.dataframe(pd.DataFrame({"Metric": ["Pooled skewness", "Pooled excess kurtosis"],
                                   "Median, seeds 40–49": v["pooled_moment_medians"],
                                   "Minimum": v["pooled_moment_ranges"][0], "Maximum": v["pooled_moment_ranges"][1]}), hide_index=True)
        st.caption("For this fit the return tail index is below 3, so even the absolute third moment is not finite. No finite fourth moment alone would not imply that skewness is undefined.")
    with tabs[2]:
        st.write("Continuous simulated records match the historical record length; independent years are never stitched together. "
                 "The observed value is located in the model's distribution over such records.")
        st.dataframe(pd.DataFrame([asdict(x) for x in v["references"]]), hide_index=True, use_container_width=True)
        st.caption("Inside the 5–95% band means compatible with this fitted-model reference, not proof of calibration. Parameters were fitted to this same history.")
    with tabs[3]:
        st.write(f"The record contains {len(returns)//252} non-overlapping years, which still share regimes. "
                 "Overlapping upper historical quantiles can repeat one crisis; the stress region has no formal PASS/FAIL gate.")
        st.dataframe(pd.DataFrame([asdict(x) for x in v["extremes"]]), hide_index=True, use_container_width=True)
        st.caption("Probabilities here are fractions (0–1). 'flagged' is a descriptive diagnostic. Record probabilities use the core's independent-block approximation (1 − p)^n; persistence limits that approximation.")
        st.write("Maxima grow with simulation budget. The comparison projects to the historical record length, not the maximum of all generated paths. Unobserved severity is model extrapolation.")
        st.write(f"{v['beyond_max_fraction']:.2%} of canonical synthetic years are more volatile than any historical rolling year. "
                 "This share describes extrapolation; by itself it is not a model failure.")
        with st.expander("Leave-out sensitivity, with its historical comparator"):
            # Preserve the core diagnostic, while respecting invariant 20 on quoted moments.
            st.dataframe(pd.DataFrame([{"Source": x.source, "Blocks removed": x.dropped,
                                        "Fraction removed": x.fraction, "Remaining volatility (%/day)": x.volatility}
                                       for x in v["leave_out"]]), hide_index=True, use_container_width=True)
            st.caption("The same removal rule applies on both sides. Single-seed pooled skewness and kurtosis are omitted here; their multi-seed evidence is in the Pooled tab.")
    with tabs[4]:
        plot(ch.histogram({"Historical daily": returns, "Canonical synthetic daily": v["paths"].reshape(-1)},
                          "Daily marginal distributions", "Daily log return (%)", log_y=True),
             "All observations are retained. Pooling is for the marginal histogram only, never the ACF.")
        plot(ch.histogram({"Historical 252-day windows": v["real"].max_drawdown*100,
                           "Synthetic 252-day paths": v["synthetic"].max_drawdown*100},
                          "Horizon-matched maximum drawdowns", "Drawdown (%)"),
             "Equal 252-day horizons. Overlapping historical windows are not independent observations; the upper quantiles are descriptive.")
        plot(ch.histogram({"Historical blocks": v["real"].es99, "Synthetic blocks": v["synthetic"].es99},
                          "Left-tail severity within 252-day blocks", "Within-block daily ES99 (% log loss)", log_y=True),
             "Each entry is daily ES estimated inside one 252-day block, not terminal-horizon ES.")
    with st.expander("Original project reports and figures (stored artifacts)"):
        for name in ("validation_report.md", "robustness_report.md", "model_comparison.md"):
            file = svc.ROOT / "reports" / name
            if file.exists():
                st.download_button(f"Download {name}", file.read_bytes(), file_name=name)
        st.caption("Stored reports can predate this run. The live gates above are recomputed from the loaded data and fit.")
        figures = sorted((svc.ROOT / "reports/figures").glob("*.png"))
        if figures:
            name = st.selectbox("Stored diagnostic figure", [p.name for p in figures])
            st.image(str(svc.ROOT / "reports/figures" / name), use_container_width=True)


def live(close, returns, g, d, details):
    st.title("Live Scenario Lab")
    st.write("Explore the conditional distribution starting from the latest fitted state at the fixed data cut-off. Generate a new set using the sidebar controls.")
    run = need_run()
    if run is None:
        return
    s, table = run.settings, run.summary
    cards([("Terminal median", f"{table['Terminal return (%)'].median():+.2f}%", "Median simple return across endpoints, not an exact price forecast."),
           ("Terminal p05", f"{table['Terminal return (%)'].quantile(.05):+.2f}%", "5th percentile of horizon simple returns."),
           ("Median max drawdown", f"{table['Max drawdown (%)'].median():.2f}%", "Path-dependent fall from the running peak."),
           ("Generation time", f"{run.elapsed:.2f}s", "Core simulation plus conditional variance trace; excludes display and validation.")])
    tabs = st.tabs(["Scenario fan", "Endpoint & path risk", "Best / worst", "Inspect a scenario", "Export"])
    with tabs[0]:
        price = st.radio("Fan chart measure", ["Price", "Cumulative simple return"], horizontal=True) == "Price"
        plot(ch.fan(run.returns, s["initial_price"], price=price),
             "Pointwise p1/p5/p25/median/p75/p95/p99 across paths. These are model-conditional bands, not simultaneous path coverage or confidence intervals for parameters.")
        plot(ch.sampled(run.returns, s["initial_price"]),
             "At most 25 evenly spaced scenario IDs are displayed. Risk calculations use every path, including extreme observations.")
    with tabs[1]:
        for col in ("Terminal return (%)", "Terminal price (USD/bbl)", "Max drawdown (%)", "Realized volatility (% annualized)"):
            plot(ch.histogram({"Scenarios": table[col]}, col, col),
                 "One observation per scenario. Terminal measures depend on the endpoint; drawdown and realized volatility use the full path.")
    with tabs[2]:
        n = st.slider("Number of ranked scenarios", 3, 25, 10)
        worst, best = st.columns(2)
        with worst:
            st.subheader("Worst terminal returns")
            st.dataframe(table.nsmallest(n, "Terminal return (%)"), hide_index=True, use_container_width=True)
        with best:
            st.subheader("Best terminal returns")
            st.dataframe(table.nlargest(n, "Terminal return (%)"), hide_index=True, use_container_width=True)
        st.caption("Rankings are conditional on this finite simulation budget. The worst or best draw is not a bound on what the fitted model can produce.")
    with tabs[3]:
        if "selected_id" not in st.session_state:
            st.session_state["selected_id"] = st.session_state.get("selected_scenario_id", 0)
        selected = int(st.number_input("Scenario ID", 0, len(table)-1, key="selected_id", step=1))
        st.session_state["selected_scenario_id"] = selected
        plot(ch.detail(run, selected), "Daily returns and conditional σ refer to days 1…H. Prices, cumulative returns and drawdowns include day zero.")
        st.dataframe(table.iloc[[selected]], hide_index=True, use_container_width=True)
    with tabs[4]:
        st.download_button("Download path summary CSV", table.to_csv(index=False), "scenario_summary.csv", "text/csv")
        st.download_button("Download run provenance JSON", llm.encode(s), "run_provenance.json", "application/json")
        if st.checkbox("Prepare full return-path CSV (may be large)"):
            st.download_button("Download all daily log returns", pd.DataFrame(run.returns).to_csv(index_label="scenario_id"), "returns.csv", "text/csv")


def risk_page(close, returns, g, d, details):
    st.title("Risk Analysis")
    run = need_run()
    if run is None:
        return
    a, b = st.columns(2)
    loss = a.slider("Custom terminal simple-loss threshold (%)", 0., 99., 25., 1.)
    dd = b.slider("Custom maximum-drawdown threshold (%)", 0., 99., 30., 1.)
    frame = risk.metrics(run.returns, run.settings["initial_price"], loss, dd)
    st.session_state["risk_table"] = frame
    tabs = st.tabs(["Terminal-horizon risk", "Daily risk", "Path-dependent risk", "Monte Carlo convergence"])
    for tab, scope in zip(tabs[:3], ["Terminal horizon", "Daily (all simulated days)", "Path-dependent"]):
        with tab:
            subset = frame[frame.scope == scope]
            st.caption("Daily risk uses percentage log losses pooled over the requested horizon. Terminal risk uses simple percentage loss over the full horizon. Neither is a leveraged futures-account P&L.")
            featured = subset.iloc[:4]
            cards([(row.metric.split(": ")[-1], f"{row.value:.2f}%", row.explanation+" Unit: "+row.unit) for row in featured.itertuples()])
            st.dataframe(subset, hide_index=True, use_container_width=True,
                         column_config={"value": st.column_config.NumberColumn("Value", format="%.4f")})
            with st.expander("Definitions for every metric"):
                for row in subset.itertuples():
                    st.markdown(f"**{row.metric}** — {row.explanation} ({row.unit})")
            if scope == "Path-dependent":
                for col in ("Worst daily log return (%)", "Realized volatility (% annualized)"):
                    plot(ch.histogram({"Scenarios": run.summary[col]}, col, col), "One worst day or realized volatility per path; no paths are concatenated.")
    with tabs[3]:
        st.write("Increasing N reduces simulation noise, but does not reduce model risk. Error often decreases roughly as 1/√N under regularity conditions; extreme-tail estimators can converge irregularly.")
        st.caption("100 / 500 / 1,000 / 5,000 / 10,000 paths. Three independent seed branches; within each branch, larger estimates use nested prefixes of whole paths. Lines show numerical variation, not confidence bands.")
        if st.button("Run convergence experiment"):
            with st.spinner("Generating three independent 10,000-path sets…"):
                st.session_state["convergence"] = svc.convergence(g, run)
        result = st.session_state.get("convergence")
        if result is not None:
            measure = st.selectbox("Convergence metric", ["VaR95", "VaR99", "ES95", "Drawdown_p95"])
            grouped = {f"Replication {rep+1}": group[measure] for rep, group in result.groupby("replication")}
            plot(ch.lines([100, 500, 1000, 5000, 10000], grouped, "Monte Carlo sensitivity", "Number of whole paths", "% simple loss / drawdown"),
                 "VaR and ES here are terminal-horizon simple-loss metrics. Drawdown p95 is path-dependent. With 100 paths, the 1% tail has only about one path.")
            st.dataframe(result, hide_index=True, use_container_width=True)


def stress_page(close, returns, g, d, details):
    st.title("Stress Test Lab")
    run = need_run()
    if run is None:
        return
    a, b = st.tabs(["A · Model-generated stress", "B · Hypothetical shock experiment"])
    with a:
        criterion = st.selectbox("Rank scenarios by", ["Terminal loss", "Drawdown", "Realized volatility", "Left-tail daily shock"])
        fraction = st.select_slider("Tail-selected share", [1, 5, 10], value=5)/100
        chosen = stress.select(run.summary, criterion, fraction)
        st.info(f"Selected {len(chosen)} of {len(run.summary)} model-generated paths by rank. This fraction is chosen by you; it is not an independently estimated probability of a named crisis.")
        plot(ch.sampled(run.returns, run.settings["initial_price"], chosen["Scenario ID"].to_numpy(), ch.RED),
             "At most 40 selected paths displayed. Their severity is generated by the fitted model, not an observed historical episode.")
        st.dataframe(chosen, hide_index=True, use_container_width=True)
    with b:
        st.write("Hypothetical deterministic experiment · no probability is assigned to these imposed shocks. "
                 "Enter simple percentage price changes; Python converts them to the model's percentage log returns.")
        preset = st.selectbox("Shock preset", list(stress.PRESETS)+["Custom"])
        default = ", ".join(str(x) for x in stress.PRESETS.get(preset, [-8., -5.]))
        text = st.text_input("Daily simple shocks (%) · comma-separated", default, key=f"shock_text_{preset}")
        multiplier = st.slider("Initial conditional volatility multiplier", 1., 5., 2. if preset == "Stressed initial volatility" else 1., .1, key=f"multiplier_{preset}")
        after = st.slider("Following days for expected variance", 10, 252, 60)
        st.caption("Latest fitted residual is held fixed; initial variance is multiplied by the square of the volatility multiplier. A 0% imposed day is an assumption, not a forecast.")
        if st.button("Apply hypothetical stress"):
            try:
                imposed = [float(x.strip()) for x in text.split(",")]
                if len(imposed) > run.settings["horizon"]:
                    raise ValueError("The shock sequence must fit inside the active scenario horizon.")
                eps, variances = svc.fitted_states(g)
                result = stress.experiment(g.params_, imposed, eps[-1], variances[-1]*multiplier**2,
                                           run.settings["initial_price"], int(after))
                baseline = svc.generate(g, run.settings["n_paths"], run.settings["horizon"], run.settings["seed"],
                                        run.settings["initial_price"], run.settings["as_of"], "latest")
                result["baseline"] = baseline
                result["multiplier"] = multiplier
                st.session_state["stress_result"] = result
            except ValueError as exc:
                st.error(str(exc))
        result = st.session_state.get("stress_result")
        if result is not None:
            st.caption(f"APPLIED EXPERIMENT · simple shocks {result['simple_shocks'].tolist()} · initial σ ×{result['multiplier']:g}. Changing controls requires Apply.")
            n = len(result["log_shocks"])
            baseline = result["baseline"]
            reference = risk.path_table(baseline.returns[:, :n], baseline.settings["initial_price"])
            cards([("Imposed terminal return", f"{result['terminal_return']:+.2f}%", f"Simple return over the {n} imposed days only."),
                   ("Imposed max drawdown", f"{result['drawdown'].max():.2f}%", "Includes initial price and the imposed shock sequence only."),
                   ("Baseline terminal median", f"{reference['Terminal return (%)'].median():+.2f}%", f"Latest-state Monte Carlo baseline over the same {n} days.")])
            plot(ch.lines(np.arange(n+1), {"Imposed price path": result["prices"],
                                          "Baseline price median": np.median(risk.prices(baseline.returns[:, :n], baseline.settings["initial_price"]), axis=0)},
                          "Prices during the imposed sequence", "Trading day", "USD / barrel"),
                 "The deterministic price line ends with the imposed sequence. No future prices are invented after the last assumed return.")
            t = np.arange(1, n+result["days_after"]+1)
            trace = np.r_[np.sqrt(result["during_variance"]), np.sqrt(result["expected_variance_after"])]
            baseline_zero = stress.experiment(g.params_, np.zeros(n), svc.fitted_states(g)[0][-1], svc.fitted_states(g)[1][-1],
                                              baseline.settings["initial_price"], result["days_after"])
            neutral_trace = np.sqrt(np.r_[baseline_zero["during_variance"], baseline_zero["expected_variance_after"]])
            fig = ch.lines(t, {"Imposed stress → √E[variance]": trace, "0% return control → √E[variance]": neutral_trace},
                           "Conditional variance response", "Trading day", "σ / √E[variance] (% per day)")
            fig.add_vline(x=n+.5, line_dash="dash", annotation_text="End of imposed returns")
            plot(fig, "During imposed days: conditional σ. Afterwards: square root of conditional expected variance, not expected σ. "
                 "It mean-reverts at the exact fitted skew-t persistence; no random innovations are drawn in this continuation.")
            plot(ch.lines(np.arange(n+1), {"Imposed drawdown": result["drawdown"]},
                          "Drawdown during the imposed sequence", "Trading day", "Drawdown (%)"),
                 "This path-dependent metric includes a loss on the first day.")


def context_for_ai(g, d, details, run):
    validation = st.session_state.get("validation")
    payload = {"model_parameters": asdict(g.params_), "structural_diagnostics": details,
               "historical_diagnostics": d["summary"],
               "limitations": ["In-sample validation only", "Volatility-memory profile mismatch",
                               "Front-month proxy and roll effects", "Fixed cut-off; no live quote",
                               "Tail extrapolation under fitted law; parameter uncertainty not simulated"],
               "validation": "Not computed in this session; open Fit & Validation."}
    if validation:
        def serial_gates(gates, pooled=False):
            rows = []
            for gate in gates:
                row = asdict(gate)
                if pooled and gate.metric in ("skewness", "excess kurtosis"):
                    row["synthetic"], row["error"] = "Unstable; use multi-seed medians", None
                rows.append(row)
            return rows
        payload["validation"] = dict(pooled=serial_gates(validation["pooled"], True),
                                     matched=serial_gates(validation["matched"]),
                                     matched_length=[asdict(x) for x in validation["references"]],
                                     extremes=[asdict(x) for x in validation["extremes"]],
                                     historical_drawdown_median_pct=float(np.median(validation["real"].max_drawdown)*100),
                                     synthetic_drawdown_median_pct=float(np.median(validation["synthetic"].max_drawdown)*100),
                                     configuration="252 days, 1000 paths, seed 42, historical_mix",
                                     pooled_moment_medians_seeds_40_49=validation["pooled_moment_medians"])
    if run:
        frame = st.session_state.get("risk_table")
        if frame is None:
            frame = risk.metrics(run.returns, run.settings["initial_price"])
        payload["scenario_settings"] = run.settings
        payload["risk_metrics"] = frame.to_dict("records")
        payload["terminal_loss_exceeds_30pct"] = risk.probability(-run.summary["Terminal return (%)"], 30)
        sid = st.session_state.get("selected_scenario_id", 0)
        payload["selected_scenario"] = run.summary.iloc[sid].to_dict()
    imposed = st.session_state.get("stress_result")
    if imposed:
        payload["stress_test"] = {k: v for k, v in imposed.items() if k != "baseline"}
        payload["stress_test"]["continuation_definition"] = "Expected variance, not expected volatility or a post-shock price forecast"
    return llm.clean(payload)


def ai_page(close, returns, g, d, details):
    st.title("Ask AI")
    st.write("Ask for an interpretation of Python-computed results. The assistant receives the context shown below and has no calculation tools.")
    payload = context_for_ai(g, d, details, st.session_state.get("run"))
    st.caption("On submission, your question and this computed summary are sent to OpenAI. The API key stays on the server. "
               "AI explanations may still be wrong; the metric tables are authoritative.")
    if not llm.available():
        st.info("AI analysis is disabled. Set OPENAI_API_KEY in the server environment to enable it. Every data, scenario and risk feature works without a key.")
    example = st.selectbox("Example question", ["Explain terminal-horizon VaR99 in simple terms.",
                           "How many scenarios lose more than 30%?", "Why is ES99 worse than VaR99?",
                           "Compare the simulated drawdown distribution with history.",
                           "Does increasing paths from 1,000 to 10,000 fix model uncertainty?",
                           "What is the main weakness of this model?", "What happens to volatility after a large negative shock?"])
    question = st.text_area("Your question", example, max_chars=2000, key=f"question_{example}")
    if st.button("Explain computed results", disabled=not llm.available()):
        try:
            with st.spinner("Interpreting the computed context…"):
                st.session_state["ai_answer"] = llm.ask(question, payload)
                st.session_state["ai_context"] = llm.encode(payload)
        except (RuntimeError, ValueError) as exc:
            st.error(str(exc))
    if st.session_state.get("ai_answer"):
        st.caption("Interpretation from the last submitted question and context. A new run clears it.")
        if st.session_state.get("ai_context") != llm.encode(payload):
            st.info("Computed context has changed since this answer. Submit again for an interpretation of the current values.")
        st.markdown(st.session_state["ai_answer"])
        with st.expander("Context used for this answer"):
            st.code(st.session_state.get("ai_context", ""), language="json")
    with st.expander("Inspect current computed context", expanded=not llm.available()):
        st.json(payload)
        st.download_button("Download computed context", llm.encode(payload), "analysis_context.json", "application/json")


GLOSSARY = {
    "Return": "A price change. The core uses 100 × log(Pt/Pt−1); the lab converts endpoints to simple percentage returns.",
    "Conditional volatility": "σt: the standard deviation conditional on the fitted state and previous shocks.",
    "Innovation": "The unpredictable residual εt = rt − μ.",
    "Standardized innovation": "zt = εt/σt, a relative shock measured in conditional standard deviations.",
    "GARCH": "A variance recursion responding to past squared residuals and past variance.",
    "GJR": "Adds a different volatility response to negative residuals.",
    "Skewed-t": "Hansen's standardized heavy-tailed innovation distribution with shape η and skew λ.",
    "Volatility clustering": "Large moves tend to occur near other large moves, regardless of direction.",
    "VaR": "A quantile of loss = minus return; the displayed scope and units determine the question it answers.",
    "ES": "Average loss at or above the empirical VaR threshold, including ties.",
    "Drawdown": "The fall from a running price peak, including the initial price.",
    "Monte Carlo": "Random simulation to estimate a distribution or a metric under an assumed model.",
    "Scenario": "One possible simulated trajectory under the fitted model, not a prediction of what will happen.",
    "Stress test": "Exploration of severe simulated paths or a hypothetical imposed shock sequence.",
}


def main():
    code = svc.cache_fingerprint()
    try:
        with st.spinner("Loading the fixed Brent sample and fitting the submitted generator…"):
            close, returns = cached_data(code)
            g = cached_fit(returns, code)
            d = cached_diagnostics(returns, code)
            details = model_details(asdict(g.params_))
    except Exception as exc:
        st.title("Brent Scenario & Risk Lab")
        st.error(f"The market-data or fit step could not complete: {exc}")
        st.info("The first launch needs Yahoo Finance access or the repository's valid cached BZ=F series. "
                "Check connectivity and retry; synthetic replacement data will not be used silently.")
        if st.button("Retry data and fit"):
            cached_data.clear()
            cached_fit.clear()
            st.rerun()
        return
    with st.sidebar:
        st.markdown("### ◈ BRENT LAB")
        st.caption("SCENARIOS / RISK / RESEARCH")
        page = st.radio("Workspace", PAGES, key="page", label_visibility="collapsed")
        st.divider()
        st.markdown("#### Scenario settings")
        with st.form("generation_controls"):
            n = st.selectbox("Number of scenarios", [100, 500, 1000, 5000, 10000], index=2)
            horizon = st.selectbox("Horizon (trading days)", [30, 60, 90, 126, 252], index=4)
            seed = st.number_input("Random seed", 0, 2147483647, 42, 1)
            mode_label = st.selectbox("Starting state", ["Latest fitted state", "Historical state mixture", "Explicit stress state"],
                                      help="Latest means the last observation in this fixed dataset, not a real-time market state.")
            sigma = st.number_input("Explicit σ (% per day)", .01, 50., 4., .1,
                                    help="Used only with Explicit stress state; variance = σ².")
            residual = st.number_input("Explicit residual (% log points)", -100., 100., -5., .5,
                                       help="Used only with Explicit stress state; this is ε, not z.")
            submit = st.form_submit_button("GENERATE SCENARIOS", use_container_width=True, type="primary")
        st.caption(f"FIXED DATA CUT-OFF\n\n{close.index[-1].date()} · ${close.iloc[-1]:.2f}/bbl\n\nThe data is not a live market feed.")
    if submit:
        try:
            mode = {"Latest fitted state": "latest", "Historical state mixture": "historical_mix", "Explicit stress state": "explicit"}[mode_label]
            with st.spinner(f"Generating {n:,} scenarios…"):
                run = svc.generate(g, n, horizon, int(seed), float(close.iloc[-1]), str(close.index[-1].date()),
                                   mode, (residual, sigma**2) if mode == "explicit" else None)
            commit_run(st.session_state, run)
            st.success(f"Generated {n:,} × {horizon} days in {run.elapsed:.2f}s. Results are available in Live Scenario Lab and Risk Analysis.")
        except ValueError as exc:
            st.error(str(exc))
    st.caption("BRENT SCENARIO & RISK LAB   /   " + page[4:].upper())
    routes = [overview, history, explainer, validation_page, live, risk_page, stress_page, ai_page]
    routes[PAGES.index(page)](close, returns, g, d, details)
    st.divider()
    with st.expander("Glossary · concepts used throughout the lab"):
        st.dataframe(pd.DataFrame(GLOSSARY.items(), columns=["Term", "Meaning"]), hide_index=True, use_container_width=True)
    st.caption("Conditional scenarios, not exact future prices · More paths reduce Monte Carlo noise, not model risk · Built on the submitted GJR-GARCH skew-t generator")


if __name__ == "__main__":
    main()
