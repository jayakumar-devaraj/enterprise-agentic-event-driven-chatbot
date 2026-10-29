"""The summary's figures block is generated from data; it must render those numbers and nothing else."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "gen_figures.py"
spec = importlib.util.spec_from_file_location("gen_figures", SCRIPT)
gen_figures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen_figures)

FIGURES = {
    "unit_tests": 3,
    "integration_tests": 2,
    "durability_tests": 1,
    "frontend_tests": 4,
    "coverage": {"percent": 91, "statements": 500, "source": "Coverage at abc1234 from CI run 7:"},
}


def test_block_renders_every_figure_and_drops_the_trailing_colon():
    text = gen_figures.block(FIGURES)
    assert text.startswith(gen_figures.START) and text.endswith(gen_figures.END)
    assert "3 unit, 2 integration" in text and "1 durability, 4 frontend" in text
    assert "91 % of 500 statements" in text
    assert 'Report header: "Coverage at abc1234 from CI run 7".' in text


def test_coverage_reads_the_total_line_and_header(tmp_path, monkeypatch):
    report = tmp_path / "coverage.txt"
    report.write_text("Coverage at abc1234:\nName  Stmts  Miss  Cover\nTOTAL   952   118    88%\n")
    monkeypatch.setattr(gen_figures, "COVERAGE", report)
    assert gen_figures.coverage() == {
        "percent": 88,
        "statements": 952,
        "source": "Coverage at abc1234:",
    }
