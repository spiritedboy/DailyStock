"""主入口：python -m src.main --slot midday|close"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from typing import List

from .ai.deepseek_client import DeepSeekClient
from .config import load_settings
from .data.fetcher import fetch_spot_prices, fetch_universe
from .decision.ranker import select_focus
from .logging_setup import setup_logging
from .models import StockEvaluation
from .notify.dingtalk import DingTalkClient
from .report.html_renderer import SLOT_DISPLAY, write_report
from .storage.repository import Repository
from .strategy.pipeline import run_pipeline

logger = logging.getLogger("dailystock")


def is_trading_day(date: datetime) -> bool:
    return date.weekday() < 5


def _select_pushed(
    evals: List[StockEvaluation],
    focus: List[StockEvaluation],
    push_top_n: int,
) -> List[StockEvaluation]:
    """推送列表 = AI 评分前 N（仅 ok 的）∪ 重点票，去重，按 AI 分降序。"""
    by_score = [e for e in evals if e.ai and e.ai.ok]
    by_score.sort(key=lambda e: (e.ai.score, e.strategy.hits), reverse=True)
    top_n = by_score[:push_top_n] if push_top_n > 0 else []

    seen = set()
    out: List[StockEvaluation] = []
    for ev in list(focus) + top_n:
        c = ev.snapshot.code
        if c in seen:
            continue
        seen.add(c)
        out.append(ev)
    out.sort(key=lambda e: (e.ai.score if e.ai else 0, e.strategy.hits), reverse=True)
    return out


def run(slot: str) -> int:
    settings = load_settings()
    setup_logging(settings.log_level, settings.log_dir)

    errors = settings.validate()
    if errors:
        for e in errors:
            logger.error("配置错误: %s", e)
        return 2

    now = datetime.now()
    run_date = now.strftime("%Y-%m-%d")
    if not is_trading_day(now):
        logger.info("非交易日(周末)，跳过执行")
        return 0

    repo = Repository(settings.sqlite_path, settings.data_dir)
    run_id = repo.new_run_id()
    logger.info("开始任务 run_id=%s slot=%s date=%s", run_id, slot, run_date)

    # 1) 选股池：成交额TopN ∪ 同花顺热榜TopN
    snapshots = fetch_universe(
        top_n_turnover=settings.top_n_turnover,
        top_n_hot=settings.top_n_hot,
        exclude_prefixes=settings.exclude_prefixes,
        exclude_name_keywords=settings.exclude_name_keywords,
    )
    if not snapshots:
        logger.warning("选股池为空，可能为非交易时段或数据源异常")

    # 2) 全量过策略
    evals = run_pipeline(snapshots, settings.strategy)
    vetoed = [e for e in evals if e.strategy.vetoed]
    candidates = [e for e in evals if e.is_candidate]
    logger.info(
        "否决=%d 候选(hits>=%d)=%d", len(vetoed), settings.strategy.min_signals, len(candidates),
    )

    candidates.sort(key=lambda e: e.strategy.hits, reverse=True)
    ai_call_list = candidates
    if settings.ai_max_candidates > 0:
        ai_call_list = candidates[: settings.ai_max_candidates]

    # 3) 候选池逐只问 DeepSeek
    ai_called = 0
    ai_allowed = 0
    if ai_call_list:
        client = DeepSeekClient(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            timeout=settings.deepseek_timeout,
            max_retry=settings.deepseek_max_retry,
        )
        for ev in ai_call_list:
            ev.ai = client.evaluate(ev)
            ai_called += 1
            if ev.ai.ok and ev.ai.allow:
                ai_allowed += 1
        logger.info("AI 完成: called=%d allowed=%d", ai_called, ai_allowed)
    else:
        logger.info("候选池为空，跳过 DeepSeek")

    # 4) 重点 + 推送列表（top N AI 分 ∪ 重点）
    focus = select_focus(evals, settings.focus_score, settings.top_k_focus)
    for ev in focus:
        ev.is_focus = True
    pushed = _select_pushed(evals, focus, settings.push_top_n)
    for ev in pushed:
        ev.is_pushed = True
    logger.info("focus=%d pushed=%d", len(focus), len(pushed))

    # 5) 上一次推送的回看（用于在 HTML 中展示涨跌幅对比）
    prev_date, prev_slot, prev_picks = repo.get_previous_pushed(run_date, slot)
    spot_now: dict = {}
    if prev_picks:
        prev_codes = [p["code"] for p in prev_picks]
        spot_now = fetch_spot_prices(prev_codes)
        logger.info(
            "上次推送 %s %s 共 %d 只，可对比 %d 只",
            prev_date, prev_slot, len(prev_picks), len(spot_now),
        )

    # 6) 生成 HTML 报告
    stats = {
        "total": len(snapshots),
        "vetoed": len(vetoed),
        "candidates": len(candidates),
        "ai_called": ai_called,
        "ai_allowed": ai_allowed,
        "min_signals": settings.strategy.min_signals,
        "push_top_n": settings.push_top_n,
    }
    local_path, report_url = write_report(
        reports_dir=settings.reports_dir,
        report_host=settings.report_host,
        run_date=run_date,
        slot=slot,
        pushed=pushed,
        stats=stats,
        prev_date=prev_date,
        prev_slot=prev_slot,
        prev_picks=prev_picks,
        spot_now=spot_now,
    )
    logger.info("HTML 报告: %s -> %s", local_path, report_url)

    # 7) 持久化
    repo.save_picks(run_id, run_date, slot, evals)
    repo.export_csv(run_date, slot, evals)

    # 8) 钉钉只推一条文本：报告 URL
    ding = DingTalkClient(
        webhook=settings.dingtalk_webhook,
        secret=settings.dingtalk_secret,
        at_mobiles=settings.dingtalk_at_mobiles,
        at_all=settings.dingtalk_at_all,
    )
    if not repo.already_notified(run_date, slot, "report", "url"):
        ai_ok_n = sum(1 for e in evals if e.ai and e.ai.ok)
        top_n_used = min(settings.push_top_n, ai_ok_n)
        text_msg = (
            f"DailyStock 选股报告 - {run_date} {SLOT_DISPLAY.get(slot, slot)}\n"
            f"推送 {len(pushed)} 只 (重点 {len(focus)} / TopAI {top_n_used})\n"
            f"{report_url}"
        )
        resp = ding.send_text(text_msg)
        ok = resp.get("errcode", -1) == 0
        repo.save_notification(run_id, run_date, slot, "report", "url", ok, str(resp))
        logger.info("钉钉推送: ok=%s", ok)
    else:
        logger.info("钉钉已发送，跳过")

    repo.save_run(
        run_id, run_date, slot,
        total=len(snapshots),
        vetoed=len(vetoed),
        candidates=len(candidates),
        ai_called=ai_called,
        ai_allowed=ai_allowed,
        focus_count=len(focus),
    )
    logger.info(
        "完成: total=%d vetoed=%d cand=%d ai=%d/%d focus=%d pushed=%d",
        len(snapshots), len(vetoed), len(candidates),
        ai_allowed, ai_called, len(focus), len(pushed),
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="DailyStock 选股推送")
    p.add_argument("--slot", choices=["midday", "close"], required=True)
    args = p.parse_args()
    try:
        return run(args.slot)
    except Exception as e:  # noqa: BLE001
        logger.exception("运行失败: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
