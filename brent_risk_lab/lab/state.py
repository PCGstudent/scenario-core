"""Session transitions, independent of Streamlit for deterministic tests."""


def commit_run(state, run):
    state["run"] = run
    for key in ("stress_result", "convergence", "ai_answer", "ai_context", "selected_id", "risk_table"):
        state.pop(key, None)
    state["selected_id"] = 0
    state["selected_scenario_id"] = 0
