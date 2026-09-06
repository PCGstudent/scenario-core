"""Interactive lab layer.

This package contains only orchestration, presentation and the risk arithmetic
that the lab adds on top of the modelling core. It deliberately re-uses
`data`, `diagnostics`, `challenger`, `metrics`, `windows` and `validation`
rather than reimplementing any of them, so that what the app displays is what
the take-home actually computes.

Nothing here is imported by the modelling core, which means the core can be
driven from a FastAPI service or a notebook without pulling in Streamlit.
"""
