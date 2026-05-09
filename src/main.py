"""主入口：python -m src.main --slot midday|close"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

from .ai.deepseek_client import DeepSeekClient
from .config import load_settings
from .data.fetcher import fetch_top_n_by_turnover
from .decision.ranker import select_focus
from .logging_setup import setup_logging
from .notify.dingtalk import DingTalkClient
from .notify.templates import render_focus_action_card, render_summary_markdown
from .storage.repository import Repository
from .strategy.pipeline import run_pipeline

logger = logging.getLogger("dailystock")


def is_trading_day(date: datetime) -> bool:
    return date.weekday() < 5


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

    # 1) 取 Top N
    snapshots = fetch_top_n_by_turnover(
        settings.top_n, settings.exclude_prefixes, settings.exclude_name_keywords
    )
    failures: dict = {}
    if not snapshots:
        failures["data"] = "行情为空，可能为非交易时段或数据源异常"

    # 2) 全量过策略，得到 evaluations
    evals = run_pipeline(snapshots, settings.strategy, min_signals=settings.strategy.min_signals)
    candidates = [e for e in evals if e.is_candidate]
    logger.info("候选池(hits>=%d): %d", settings.strategy.min_signals, len(candidates))

    # 按 hits 降序作为 AI 调用顺序，必要时再截断
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

    # 4) 决策分层
    focus = select_focus(evals, settings.focus_score, settings.top_k_focus)

    # 5) 持久化
    repo.save_picks(run_id, run_date, slot, evals)
    repo.export_csv(run_date, slot, evals)

    # 6) 推送
    ding = DingTalkClient(
        webhook=settings.dingtalk_webhook,
        secret=settings.dingtalk_secret,
        at_mobiles=settings.dingtalk_at_mobiles,
        at_all=settings.dingtalk_at_all,
    )

    title, text = render_summary_markdown(
        slot=slot,
        total=len(snapshots),
        candidates=len(candidates),
        ai_called=ai_called,
        ai_allowed=ai_allowed,
        focus=focus,
        failures=failures or None,
    )
    if not repo.already_notified(run_date, slot, "summary", "summary"):
        resp = ding.send_markdown(title, text)
        ok = resp.get("errcode", -1) == 0
        repo.save_notification(run_id, run_date, slot, "summary", "summary", ok, str(resp))
    else:
        logger.info("汇总已发送，跳过")

    for ev in focus:
        target = ev.snapshot.code
        if repo.already_notified(run_date, slot, "focus", target):
            logger.info("重点 %s 已发送，跳过", target)
            continue
        t, content, st_, su = render_focus_action_card(ev, slot)
        resp = ding.send_action_card(t, content, st_, su)
        ok = resp.get("errcode", -1) == 0
        repo.save_notification(run_id, run_date, slot, "focus", target, ok, str(resp))

    repo.save_run(
        run_id, run_date, slot,
        total=len(snapshots),
        candidates=len(candidates),
        ai_called=ai_called,
        ai_allowed=ai_allowed,
        focus_count=len(focus),
    )
    logger.info(
        "完成: total=%d cand=%d ai_called=%d ai_allowed=%d focus=%d",
        len(snapshots), len(candidates), ai_called, ai_allowed, len(focus),
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
