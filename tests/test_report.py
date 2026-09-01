from xtra_takehome.challenger import GjrSkewTParams
from xtra_takehome.diagnostics import DiagnosticSummary
from xtra_takehome.report import write_report
from xtra_takehome.validation import Gate


def test_final_report_names_selected_model_and_failure(tmp_path):
    summary = DiagnosticSummary(
        n=3000,
        mean=0.01,
        std=2.0,
        skew=-0.8,
        excess_kurtosis=10.0,
        min_return=-12.0,
        max_return=9.0,
        q01=-6.0,
        q05=-3.0,
        q95=3.0,
        q99=5.5,
        max_abs_return_acf=0.04,
        mean_abs_squared_acf=0.12,
        student_t_df=4.5,
        student_t_loc=0.0,
        student_t_scale=1.5,
    )
    params = GjrSkewTParams(
        mu=0.01,
        omega=0.05,
        alpha=0.06,
        gamma=0.04,
        beta=0.90,
        eta=6.0,
        lam=-0.10,
    )
    gates = [
        Gate(
            metric="volatility",
            real=2.0,
            synthetic=2.3,
            error=0.15,
            threshold=0.10,
            error_type="relative",
            passed=False,
        )
    ]

    out = tmp_path / "validation_report.md"
    write_report(out, summary=summary, params=params, gates=gates)
    text = out.read_text(encoding="utf-8")

    assert "GJR-GARCH(1,1,1)" in text
    assert "effective variance persistence" in text
    assert "volatility" in text
    assert "FAIL" in text
    assert "fourth-moment" in text
