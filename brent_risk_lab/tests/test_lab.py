import json
from dataclasses import asdict

import numpy as np
import pytest
from statsmodels.tsa.stattools import acf

from lab import risk, services, stress, llm
from lab.state import commit_run
from xtra_takehome.challenger import GjrSkewTGenerator, GjrSkewTParams
from xtra_takehome.metrics import mean_squared_return_acf, var_es


@pytest.fixture
def generator():
    g = GjrSkewTGenerator()
    g.params_ = GjrSkewTParams(.02, .05, .06, .08, .88, 6., -.15)
    g._residuals = np.array([-1.5, -.3, .2, .8, 1.2])
    g._variances = np.array([1.8, .9, .7, 1., 1.4])
    return g


def test_simple_return_and_terminal_price_reconstruction():
    paths = 100*np.log1p(np.array([[-.1, .2], [.1, -.2]]))
    np.testing.assert_allclose(risk.prices(paths, 100), [[100, 90, 108], [100, 110, 88]])
    np.testing.assert_allclose(risk.cumulative_return(paths), [[0, -10, 8], [0, 10, -12]], atol=1e-12)
    t = risk.path_table(paths, 100)
    np.testing.assert_allclose(t["Terminal price (USD/bbl)"], [108, 88])


def test_day_one_drawdown_and_path_order():
    paths = np.array([[-10., 5.], [5., -10.]])
    dd = risk.drawdown_paths(paths)
    assert dd[0, 1] == pytest.approx(100*(1-np.exp(-.1)))
    np.testing.assert_allclose(risk.path_table(paths, 100)["Max drawdown (%)"], dd.max(axis=1))
    np.testing.assert_allclose(dd[:, 0], 0)


def test_var_es_scope_units_and_positive_loss_convention():
    paths = np.array([[-5., -4.], [-3., -2.], [-1., 0.], [1., 2.], [3., 4.]])
    m = risk.metrics(paths, 100).set_index("metric")
    for level in (.95, .99):
        v, e = var_es(paths.reshape(-1), level)
        assert m.loc[f"Daily (all simulated days): VaR{int(level*100)}", "value"] == v
        assert m.loc[f"Daily (all simulated days): ES{int(level*100)}", "value"] == e
        vt, et = var_es(100*np.expm1(paths.sum(axis=1)/100), level)
        assert m.loc[f"Terminal horizon: VaR{int(level*100)}", "value"] == vt
        assert m.loc[f"Terminal horizon: ES{int(level*100)}", "value"] == et
        assert e >= v >= 0 and et >= vt >= 0
    assert m.loc["Daily (all simulated days): VaR95", "unit"] == "% log loss"


def test_threshold_probabilities_count_strict_exceedance():
    assert risk.probability([0, 10, 20, 30], 20) == dict(count=1, n=4, probability_pct=25.)
    assert risk.probability([1, 2], 4)["probability_pct"] == 0
    assert risk.probability([1, 2], 0)["probability_pct"] == 100


@pytest.mark.parametrize("mode", ["latest", "historical_mix", "explicit"])
def test_scenario_shapes_seed_reproducibility_and_no_mutation(generator, mode):
    original = [x.copy() for x in services.fitted_states(generator)]
    args = dict(n_paths=12, horizon=30, p0=80, as_of="2026-08-31", mode=mode,
                explicit=(-3., 4.) if mode == "explicit" else None)
    a = services.generate(generator, seed=42, **args)
    b = services.generate(generator, seed=42, **args)
    c = services.generate(generator, seed=43, **args)
    assert a.returns.shape == a.variances.shape == (12, 30)
    np.testing.assert_array_equal(a.returns, b.returns)
    np.testing.assert_array_equal(a.variances, b.variances)
    assert not np.array_equal(a.returns, c.returns)
    assert np.isfinite(a.returns).all()
    for before, after in zip(original, services.fitted_states(generator)):
        np.testing.assert_array_equal(before, after)


def test_adapter_matches_original_core_and_trace(generator):
    baseline = generator.simulate(30, 12, 42)
    run = services.generate(generator, 12, 30, 42, 80, "2026-08-31", "historical_mix")
    np.testing.assert_array_equal(run.returns, baseline)
    p = generator.params_
    eps = run.returns[:, :-1]-p.mu
    expected = p.omega + p.alpha*eps**2 + p.gamma*(eps < 0)*eps**2 + p.beta*run.variances[:, :-1]
    np.testing.assert_allclose(run.variances[:, 1:], expected)


def test_latest_initial_variance_is_advanced_exactly_once(generator):
    run = services.generate(generator, 12, 30, 42, 80, "2026-08-31")
    p = generator.params_
    expected = p.omega + p.alpha*1.2**2 + p.beta*1.4
    np.testing.assert_allclose(run.variances[:, 0], expected)


def test_acf_uses_separate_paths_not_concatenation():
    rng = np.random.default_rng(4)
    paths = np.vstack([rng.normal(0, .1, 80), rng.normal(0, 10, 80)])
    actual = mean_squared_return_acf(paths, 5)
    expected = np.mean([acf(row**2, nlags=5, fft=True) for row in paths], axis=0)
    np.testing.assert_allclose(actual, expected)
    assert not np.allclose(actual, acf(paths.reshape(-1)**2, nlags=5, fft=True))


def test_deterministic_stress_conversion_and_exact_recursion(generator):
    p = generator.params_
    result = stress.experiment(p, [-10, -5], 0., 4., 100, 3)
    np.testing.assert_allclose(result["prices"], [100, 90, 85.5])
    assert result["terminal_return"] == pytest.approx(-14.5)
    assert result["drawdown"].max() == pytest.approx(14.5)
    v1 = p.omega+p.beta*4
    residual = 100*np.log(.9)-p.mu
    v2 = p.omega+(p.alpha+p.gamma)*residual**2+p.beta*v1
    assert result["during_variance"][0] == pytest.approx(v1)
    assert result["during_variance"][1] == pytest.approx(v2)
    v3 = p.omega+(p.alpha+p.gamma)*(100*np.log(.95)-p.mu)**2+p.beta*v2
    assert result["expected_variance_after"][0] == pytest.approx(v3)
    assert result["expected_variance_after"][1] == pytest.approx(p.omega+p.effective_persistence*v3)
    again = stress.experiment(p, [-10, -5], 0., 4., 100, 3)
    np.testing.assert_array_equal(result["expected_variance_after"], again["expected_variance_after"])


def test_invalid_stress_and_price_inputs(generator):
    for shocks in ([], [-100], [np.nan]):
        with pytest.raises(ValueError):
            stress.experiment(generator.params_, shocks, 0, 4, 100)
    with pytest.raises(ValueError):
        risk.prices([[1]], -2)
    with pytest.raises(ValueError):
        risk.prices([[float("inf")]], 100)


def test_extreme_price_overflow_is_not_silently_clipped():
    with pytest.raises(ValueError, match="no paths were clipped"):
        risk.prices([[100000]], 100)


def test_regeneration_clears_dependent_state():
    state = dict(run="old", stress_result=1, convergence=1, ai_answer=1, selected_id=900, risk_table=1, validation="keep")
    commit_run(state, "new")
    assert state == dict(run="new", selected_id=0, selected_scenario_id=0, validation="keep")


def test_convergence_uses_independent_branches_and_nested_whole_paths(generator):
    run = services.generate(generator, 10, 30, 42, 80, "date")
    frame = services.convergence(generator, run, counts=(10, 20), replications=2)
    assert frame.shape == (4, 6)
    assert list(frame.paths) == [10, 20, 10, 20]
    assert not np.allclose(frame.iloc[0, 2:], frame.iloc[2, 2:])


def test_llm_disabled_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert not llm.available()
    with pytest.raises(ValueError, match="disabled"):
        llm.ask("anything", {})
    assert json.loads(llm.encode({"a": np.float64(np.inf)})) == {"a": None}


def test_llm_only_sends_computed_context_and_parses_responses(monkeypatch):
    import io
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-not-a-key")
    sent = {}
    def fake_open(request, timeout):
        sent.update(json.loads(request.data))
        return io.BytesIO(json.dumps({"output": [{"type": "message", "content": [
            {"type": "output_text", "text": "See Terminal horizon: VaR99."}]}]}).encode())
    monkeypatch.setattr(llm, "urlopen", fake_open)
    assert llm.ask("Explain", {"metric": 5}) == "See Terminal horizon: VaR99."
    assert sent["store"] is False
    assert "\"metric\": 5" in sent["input"]
    assert "Do not calculate" in sent["instructions"]
    assert "tools" not in sent


def test_ranked_stress_preserves_scenario_ids():
    paths = np.array([[-1, -2], [-20, -10], [3, 4]])
    chosen = stress.select(risk.path_table(paths, 100), "Terminal loss", .01)
    assert chosen["Scenario ID"].tolist() == [1]
