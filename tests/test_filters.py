"""一字板与行业去集中过滤测试。"""
from src.models import StockSnapshot, IndicatorBundle, StrategyResult, StockEvaluation
from src.strategy.filters import (
    diversify_by_industry,
    filter_unbuyable,
    is_daily_limit_up,
    is_yiziban,
    _limit_pct,
)


def _mk(code: str, **kw) -> StockEvaluation:
    snap = StockSnapshot(code=code, name=code, **kw)
    return StockEvaluation(snapshot=snap, indicators=IndicatorBundle(), strategy=StrategyResult())


def test_limit_pct_boards():
    assert _limit_pct("000001") == 10.0
    assert _limit_pct("300001") == 20.0
    assert _limit_pct("688001") == 20.0
    assert _limit_pct("830001") == 30.0


def test_yiziban_main_board():
    e = _mk("600001", price=11.0, pct_change=10.0, open=11.0, high=11.0, low=11.0, pre_close=10.0)
    assert is_daily_limit_up(e.snapshot)
    assert is_yiziban(e.snapshot)


def test_not_yiziban_when_no_limit_up():
    e = _mk("600001", price=10.5, pct_change=5.0, open=10.0, high=10.6, low=9.9, pre_close=10.0)
    assert not is_daily_limit_up(e.snapshot)
    assert not is_yiziban(e.snapshot)


def test_not_yiziban_when_intraday_swing():
    # 涨停但盘中波动大
    e = _mk("600001", price=11.0, pct_change=10.0, open=10.2, high=11.0, low=10.1, pre_close=10.0)
    assert is_daily_limit_up(e.snapshot)
    assert not is_yiziban(e.snapshot)


def test_filter_unbuyable():
    yzb = _mk("600001", price=11.0, pct_change=10.0, open=11.0, high=11.0, low=11.0, pre_close=10.0)
    ok = _mk("600002", price=10.5, pct_change=3.0, open=10.0, high=10.6, low=9.9, pre_close=10.0)
    kept, removed = filter_unbuyable([yzb, ok])
    assert [e.snapshot.code for e in kept] == ["600002"]
    assert [e.snapshot.code for e in removed] == ["600001"]


def test_diversify_by_industry_caps():
    a, b, c, d, e = (_mk("60000{}".format(i)) for i in range(5))
    industry = {"600000": "X", "600001": "X", "600002": "X", "600003": "Y", "600004": "Y"}
    kept, removed = diversify_by_industry([a, b, c, d, e], industry, max_per_industry=2)
    codes = [x.snapshot.code for x in kept]
    assert codes == ["600000", "600001", "600003", "600004"]
    assert [x.snapshot.code for x in removed] == ["600002"]


def test_diversify_unknown_industry_keeps_all():
    a, b = _mk("600000"), _mk("600001")
    kept, removed = diversify_by_industry([a, b], {}, max_per_industry=1)
    assert len(kept) == 2 and not removed
