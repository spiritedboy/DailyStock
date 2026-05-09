"""HTML 渲染与 index 生成 smoke 测试。"""
from datetime import datetime
from pathlib import Path

from src.models import (
    AiDecision, IndicatorBundle, StockEvaluation, StockSnapshot, StrategyResult,
)
from src.report.html_renderer import (
    render_html, report_paths, write_index, write_report,
)


def _ev(code: str = "600000") -> StockEvaluation:
    return StockEvaluation(
        snapshot=StockSnapshot(code=code, name="测试", price=10.0, pct_change=2.5,
                               turnover=1.2e9, amplitude=3.5, sources=["turnover"]),
        indicators=IndicatorBundle(ma5=10, ma10=9.8, ma20=9.5, rsi14=58, macd_hist=0.05,
                                   volume_ratio=1.5, bias10=2.0, bias20=5.3, pct_20d=8.2,
                                   high52w=12.0),
        strategy=StrategyResult(hits=3, signals=["MA多头", "MACD金叉", "RSI回升"]),
        ai=AiDecision(score=85, allow=True, reason="趋势良好", ok=True),
        is_focus=True, is_pushed=True,
    )


def test_report_paths():
    local, url = report_paths(Path("/tmp/r"), "http://x", "2025-05-09", "midday")
    assert local == Path("/tmp/r/2025-05/05-09-noon.html")
    assert url == "http://x/2025-05/05-09-noon.html"


def test_render_html_smoke():
    html_text = render_html(
        "2025-05-09", "close", [_ev()],
        stats={"total": 100, "vetoed": 30, "candidates": 12, "ai_called": 8,
               "ai_allowed": 5, "min_signals": 2, "push_top_n": 5},
        prev_date="2025-05-08", prev_slot="close", prev_picks=[], spot_now={},
        overall={"days": 30, "period": 5, "n": 10, "avg": 1.5, "win": 60.0,
                 "max": 8.0, "min": -3.0},
        signal_rows=[{"signal": "MA多头", "n": 5, "avg": 2.1, "win": 60.0}],
        ai_rows=[{"bucket": "80-89", "n": 5, "avg": 1.8, "win": 60.0}],
    )
    assert "DailyStock 报告" in html_text
    assert "MA多头" in html_text
    assert "echarts" not in html_text  # no klines_map → no script


def test_write_report(tmp_path: Path):
    local, url = write_report(
        reports_dir=tmp_path, report_host="http://x",
        run_date="2025-05-09", slot="midday", pushed=[_ev()],
        stats={"total": 50, "vetoed": 10, "candidates": 5, "ai_called": 3,
               "ai_allowed": 2, "min_signals": 2, "push_top_n": 5},
        prev_date="", prev_slot="", prev_picks=[], spot_now={},
    )
    assert local.exists()
    assert local.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


def test_write_index(tmp_path: Path):
    runs = [
        {"run_date": "2025-05-09", "run_slot": "close", "total": 100,
         "candidates": 10, "focus_count": 3, "vetoed": 30},
        {"run_date": "2025-05-08", "run_slot": "midday", "total": 90,
         "candidates": 8, "focus_count": 2, "vetoed": 25},
    ]
    out = write_index(tmp_path, runs)
    assert out.exists()
    txt = out.read_text(encoding="utf-8")
    assert "2025-05" in txt
    month_idx = tmp_path / "2025-05" / "index.html"
    assert month_idx.exists()
