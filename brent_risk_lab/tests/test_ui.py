"""Offline Streamlit interaction test; no Yahoo or LLM requests."""
from pathlib import Path

import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest

from lab import services
from xtra_takehome.challenger import GjrSkewTGenerator, GjrSkewTParams


def test_pages_generation_risk_stress_and_regeneration(monkeypatch):
    rng = np.random.default_rng(50)
    r = pd.Series(rng.standard_t(6, 2600), index=pd.bdate_range("2010-01-04", periods=2600))
    close = 70*np.exp(r.cumsum()/100)
    g = GjrSkewTGenerator()
    g.params_ = GjrSkewTParams(.02, .05, .06, .08, .88, 6., -.15)
    g._residuals = r.to_numpy()-.02
    g._variances = np.full(len(r), 2.)
    g.fit_summary_ = "Offline UI test fitted-state fixture"
    monkeypatch.setattr(services, "load_data", lambda: (close, r))
    monkeypatch.setattr(services, "fit", lambda _: g)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = AppTest.from_file(str(Path(__file__).parents[1]/"app.py"), default_timeout=120).run()
    assert not app.exception
    page = app.radio(key="page")
    for label in ("02  Historical Data", "03  Model Explainer", "08  Ask AI"):
        page.set_value(label).run()
        assert not app.exception, label
    app.radio(key="page").set_value("04  Fit & Validation").run()
    assert not app.exception
    assert len(app.session_state["validation"]["matched"]) == 13
    app.radio(key="page").set_value("05  Live Scenario Lab").run()
    # Sidebar widgets remain available on every page.
    next(x for x in app.selectbox if x.label == "Number of scenarios").set_value(100)
    next(x for x in app.selectbox if x.label == "Horizon (trading days)").set_value(30)
    next(x for x in app.button if x.label == "GENERATE SCENARIOS").click().run()
    assert not app.exception
    assert app.session_state["run"].returns.shape == (100, 30)
    app.number_input(key="selected_id").set_value(99).run()
    assert not app.exception
    app.radio(key="page").set_value("06  Risk Analysis").run()
    assert not app.exception
    assert app.session_state["selected_scenario_id"] == 99
    app.radio(key="page").set_value("07  Stress Test Lab").run()
    next(x for x in app.button if x.label == "Apply hypothetical stress").click().run()
    assert not app.exception
    assert "stress_result" in app.session_state
    app.radio(key="page").set_value("05  Live Scenario Lab").run()
    next(x for x in app.button if x.label == "GENERATE SCENARIOS").click().run()
    assert not app.exception
    assert app.session_state["selected_id"] == 0
    assert "stress_result" not in app.session_state
