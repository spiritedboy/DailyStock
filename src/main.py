"""主入口：python -m src.main run --slot midday|close

集成：大盘过滤、行业去集中、一字板剔除、K线缓存+并发、AI 缓存+预算、
推送跟踪、报告 index、邮件兜底、结构化日志。
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import sys
import traceback
from datetime import datetime
from typing import List

from .ai.deepseek_client import DeepSeekClient
from .config import Settings, load_settings
from .data.cache import KlineCache
from .data.fetcher import fetch_klines_concurrent, fetch_spot_prices, fetch_universe
from .data.industry import load_industry_map
from .data.market import assess_market
from .decision.ranker import select_focus
from .logging_setup import setup_logging
from .metrics import append_metrics
from .models import StockEvaluation
from .notify.dingtalk import DingTalkClient
from .notify.email_client import EmailClient
from .report.html_renderer import SLOT_DISPLAY, write_index, write_report
from .storage.repository import Repository
from .strategy.filters import diversify_by_industry, filter_unbuyable, is_daily_limit_up
from .strategy.pipeline import run_pipeline
from .tracking.tracker import (
    ai_calibration,
    render_overall_stats,
    signal_breakdown,
    update_pick_returns,
)

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


def _send_email_fallback(settings: Settings, subject: str, body: str) -> bool:
    if not settings.smtp_host:
        return False
    em = EmailClient(
        host=settings.smtp_host, port=settings.smtp_port,
        user=settings.smtp_user, password=settings.smtp_password,
        sender=settings.smtp_sender, recipients=settings.smtp_recipients,
        use_ssl=settings.smtp_use_ssl,
    )
    if not em.enabled:
        return False
    ok = em.send(subject, body)
    logger.info("邮件兜底发送: ok=%s", ok)
    return ok


def run(slot: str, dryrun: bool = False, no_ai_cache: bool = False) -> int:
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
        if dryrun:
            logger.warning("非交易日 (%s)，dryrun 继续执行", now.strftime("%a"))
        else:
            logger.info("非交易日(周末)，跳过执行")
            return 0

    repo = Repository(settings.sqlite_path, settings.data_dir)
    run_id = repo.new_run_id()
    logger.info("开始任务 run_id=%s slot=%s date=%s dryrun=%s ai_cache=%s", run_id, slot, run_date, dryrun, not no_ai_cache)

    try:
        return _run_inner(settings, repo, run_id, run_date, slot, dryrun, no_ai_cache=no_ai_cache)
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc()
        logger.exception("运行失败: %s", e)
        body = f"DailyStock {run_date} {slot} 失败:\n{e}\n\n{tb}"
        _send_email_fallback(settings, f"[DailyStock] 运行失败 {run_date} {slot}", body)
        if settings.dingtalk_webhook:
            try:
                ding = DingTalkClient(
                    webhook=settings.dingtalk_webhook,
                    secret=settings.dingtalk_secret,
                )
                ding.send_text(f"[DailyStock] {run_date} {slot} 运行失败:\n{e}")
            except Exception:  # noqa: BLE001
                pass
        return 1


def _run_inner(
    settings: Settings, repo: Repository, run_id: str, run_date: str, slot: str, dryrun: bool,
    no_ai_cache: bool = False,
) -> int:
    # 1) 选股池
    snapshots, spot_df = fetch_universe(
        top_n_turnover=settings.top_n_turnover,
        top_n_hot=settings.top_n_hot,
        exclude_prefixes=settings.exclude_prefixes,
        exclude_name_keywords=settings.exclude_name_keywords,
        spot_source=settings.spot_source,
    )
    if len(snapshots) < settings.universe_min_size:
        logger.warning(
            "选股池过小 (%d < %d)，可能数据源异常，中止",
            len(snapshots), settings.universe_min_size,
        )
        return 3

    # 1.5) 候选前剔除涨停票：避免在不可买入标的上浪费策略/AI算力
    if settings.limit_up_filter_enabled:
        kept_snapshots = []
        removed_limit_codes = []
        for s in snapshots:
            if is_daily_limit_up(s):
                removed_limit_codes.append(s.code)
            else:
                kept_snapshots.append(s)
        if removed_limit_codes:
            logger.info("候选前剔除涨停票 %d 只: %s", len(removed_limit_codes), removed_limit_codes[:20])
        snapshots = kept_snapshots
        if not snapshots:
            logger.warning("候选前剔除涨停后样本为空，中止")
            return 3

    # 1.6) 价格上限过滤（一手 = 100 股，价格太高买不起）
    if settings.max_price > 0:
        kept = []
        removed_price = []
        for s in snapshots:
            if s.price > 0 and s.price > settings.max_price:
                removed_price.append((s.code, s.price))
            else:
                kept.append(s)
        if removed_price:
            logger.info(
                "候选前剔除价格 > %.2f 的票 %d 只: %s",
                settings.max_price, len(removed_price), removed_price[:10],
            )
        snapshots = kept
        if not snapshots:
            logger.warning("价格过滤后样本为空，中止")
            return 3

    # 2) 大盘环境
    above_ma20, market_info = assess_market()
    market_bad = (settings.market_filter_enabled and above_ma20 is False)
    if market_bad:
        logger.info("大盘弱势 %s，仅推送 AI 分>=%d 的票", market_info, settings.market_filter_min_score)

    # 3) 行业映射
    industry_map = {}
    if settings.industry_diversify_enabled:
        try:
            industry_map = load_industry_map(
                cache_path=settings.industry_cache_path,
                ttl_days=settings.industry_cache_ttl_days,
            )
            for s in snapshots:
                ind = industry_map.get(s.code, "")
                if ind:
                    s.extra["industry"] = ind
        except Exception as e:  # noqa: BLE001
            logger.warning("加载行业映射失败: %s", e)

    # 4) 全量过策略（K线缓存 + 并发）
    cache = KlineCache(settings.klines_cache_dir) if settings.klines_cache_enabled else None
    evals = run_pipeline(
        snapshots, settings.strategy,
        cache=cache,
        max_workers=settings.klines_workers,
        klines_days=settings.klines_days,
    )
    vetoed = [e for e in evals if e.strategy.vetoed]
    min_signals = settings.strategy.min_signals
    candidates = [e for e in evals if e.is_candidate(min_signals)]
    logger.info("否决=%d 候选=%d (阈值 hits>=%d)", len(vetoed), len(candidates), min_signals)

    candidates.sort(key=lambda e: e.strategy.hits, reverse=True)
    ai_call_list = candidates
    if settings.ai_max_candidates > 0:
        ai_call_list = candidates[: settings.ai_max_candidates]

    # 5) DeepSeek（仅缓存，不限额）
    ai_called = 0
    ai_allowed = 0
    if ai_call_list and settings.deepseek_api_key:
        client = DeepSeekClient(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            timeout=settings.deepseek_timeout,
            max_retry=settings.deepseek_max_retry,
            repo=repo,
            use_cache=not no_ai_cache,
        )
        workers = max(1, min(settings.ai_workers, len(ai_call_list)))
        if ai_call_list:
            logger.info("AI 并发开始: candidates=%d workers=%d", len(ai_call_list), workers)
            with ThreadPoolExecutor(max_workers=workers) as ex:
                future_map = {ex.submit(client.evaluate, ev, run_date): ev for ev in ai_call_list}
                done = 0
                for fut in as_completed(future_map):
                    ev = future_map[fut]
                    try:
                        ev.ai = fut.result()
                    except Exception as e:  # noqa: BLE001
                        logger.warning("AI 任务异常 %s: %s", ev.snapshot.code, e)
                        ev.ai = None
                    ai_called += 1
                    if ev.ai and ev.ai.ok and ev.ai.allow:
                        ai_allowed += 1
                    done += 1
                    if done % 5 == 0 or done == len(ai_call_list):
                        logger.info("AI 并发进度 %d/%d", done, len(ai_call_list))
        logger.info("AI 完成: called=%d allowed=%d", ai_called, ai_allowed)
    else:
        logger.info("候选池为空或未配置 API Key，跳过 DeepSeek")

    # 6) 重点 + 推送 + 涨停兜底剔除 + 行业去集中
    focus = select_focus(evals, settings.focus_score, settings.top_k_focus)
    for ev in focus:
        ev.is_focus = True
    raw_pushed = _select_pushed(evals, focus, settings.push_top_n)

    if settings.limit_up_filter_enabled:
        kept, removed_limit = filter_unbuyable(raw_pushed)
        if removed_limit:
            logger.info("推送前兜底剔除涨停票 %d 只: %s", len(removed_limit), [e.snapshot.code for e in removed_limit])
        raw_pushed = kept

    if market_bad:
        raw_pushed = [
            e for e in raw_pushed
            if (e.ai and e.ai.score >= settings.market_filter_min_score)
        ]
        logger.info("大盘弱势过滤后剩余 %d 只", len(raw_pushed))

    if settings.industry_diversify_enabled and industry_map:
        kept, removed_ind = diversify_by_industry(
            raw_pushed, industry_map,
            max_per_industry=settings.industry_max_per_industry,
        )
        if removed_ind:
            logger.info("行业去集中剔除 %d 只", len(removed_ind))
        raw_pushed = kept

    pushed = raw_pushed
    for ev in pushed:
        ev.is_pushed = True
    logger.info("focus=%d pushed=%d", len(focus), len(pushed))

    # 7) 上一次推送回看
    prev_date, prev_slot, prev_picks = repo.get_previous_pushed(run_date, slot)
    spot_now: dict = {}
    if prev_picks:
        prev_codes = [p["code"] for p in prev_picks]
        spot_now = fetch_spot_prices(prev_codes, spot_df=spot_df, spot_source=settings.spot_source)

    # 8) 跟踪：更新历史推送的 T+N 收益
    if settings.tracking_enabled and not dryrun:
        try:
            def _fetch(code: str, days: int = 80):
                return fetch_klines_concurrent([code], days=days, cache=cache, max_workers=1).get(code)
            n_upd = update_pick_returns(repo, _fetch)
            logger.info("跟踪表更新条数: %d", n_upd)
        except Exception as e:  # noqa: BLE001
            logger.warning("更新跟踪表失败: %s", e)

    # 9) 报告附加数据
    overall = render_overall_stats(repo, days=settings.tracking_lookback_days, period=5) if settings.tracking_enabled else None
    sig_rows = signal_breakdown(repo, days=settings.tracking_lookback_days) if settings.tracking_enabled else []
    ai_rows = ai_calibration(repo, days=settings.tracking_lookback_days) if settings.tracking_enabled else []

    klines_map = {}
    if settings.report_kline_chart and pushed:
        try:
            klines_map = fetch_klines_concurrent(
                [e.snapshot.code for e in pushed],
                days=60, cache=cache,
                max_workers=min(settings.klines_workers, max(1, len(pushed))),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("拉推送票 K 线失败: %s", e)

    # 10) HTML 报告
    stats = {
        "total": len(snapshots), "vetoed": len(vetoed),
        "candidates": len(candidates),
        "ai_called": ai_called, "ai_allowed": ai_allowed,
        "min_signals": settings.strategy.min_signals,
        "push_top_n": settings.push_top_n,
        "market_above_ma20": above_ma20,
    }
    local_path, report_url = write_report(
        reports_dir=settings.reports_dir,
        report_host=settings.report_host,
        run_date=run_date, slot=slot,
        pushed=pushed, stats=stats,
        prev_date=prev_date, prev_slot=prev_slot,
        prev_picks=prev_picks, spot_now=spot_now,
        overall=overall, signal_rows=sig_rows, ai_rows=ai_rows,
        klines_map=klines_map,
    )
    logger.info("HTML 报告: %s -> %s", local_path, report_url)

    # 11) 持久化
    if not dryrun:
        repo.save_picks(run_id, run_date, slot, evals)
        repo.export_csv(run_date, slot, evals)
        repo.save_run(
            run_id, run_date, slot,
            total=len(snapshots), vetoed=len(vetoed),
            candidates=len(candidates),
            ai_called=ai_called, ai_allowed=ai_allowed,
            focus_count=len(focus),
        )
        try:
            runs = repo.list_reports(limit=500)
            write_index(settings.reports_dir, runs)
        except Exception as e:  # noqa: BLE001
            logger.warning("写 index.html 失败: %s", e)

    # 12) 钉钉（dryrun 也推送；dryrun 不做去重、不落库）
    if settings.dingtalk_webhook and (dryrun or not repo.already_notified(run_date, slot, "report", "url")):
        ding = DingTalkClient(
            webhook=settings.dingtalk_webhook,
            secret=settings.dingtalk_secret,
            at_mobiles=settings.dingtalk_at_mobiles,
            at_all=settings.dingtalk_at_all,
        )
        ai_ok_n = sum(1 for e in evals if e.ai and e.ai.ok)
        top_n_used = min(settings.push_top_n, ai_ok_n)
        title_prefix = "[DRYRUN] " if dryrun else ""
        text_msg = (
            f"{title_prefix}DailyStock 选股报告 - {run_date} {SLOT_DISPLAY.get(slot, slot)}\n"
            f"推送 {len(pushed)} 只 (重点 {len(focus)} / TopAI {top_n_used})\n"
            f"{report_url}"
        )
        try:
            resp = ding.send_text(text_msg)
            ok = resp.get("errcode", -1) == 0
            if not dryrun:
                repo.save_notification(run_id, run_date, slot, "report", "url", ok, str(resp))
            logger.info("钉钉推送%s: ok=%s", "(dryrun)" if dryrun else "", ok)
            if not ok:
                _send_email_fallback(
                    settings, f"[DailyStock] 钉钉失败 {run_date} {slot}",
                    f"钉钉返回: {resp}\n报告: {report_url}",
                )
        except Exception as e:  # noqa: BLE001
            logger.error("钉钉异常: %s", e)
            _send_email_fallback(
                settings, f"[DailyStock] 钉钉异常 {run_date} {slot}",
                f"异常: {e}\n报告: {report_url}",
            )

    # 13) 结构化日志
    append_metrics(settings.log_dir, {
        "run_id": run_id, "run_date": run_date, "slot": slot,
        "total": len(snapshots), "vetoed": len(vetoed), "candidates": len(candidates),
        "ai_called": ai_called, "ai_allowed": ai_allowed,
        "focus": len(focus), "pushed": len(pushed),
        "market_above_ma20": above_ma20,
        "report_url": report_url,
    })

    logger.info(
        "完成: total=%d vetoed=%d cand=%d ai=%d/%d focus=%d pushed=%d",
        len(snapshots), len(vetoed), len(candidates),
        ai_allowed, ai_called, len(focus), len(pushed),
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="DailyStock 选股推送")
    sub = p.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="正常运行")
    p_run.add_argument("--slot", choices=["midday", "close"], required=True)
    p_run.add_argument("--no-ai-cache", action="store_true", help="忽略 AI 结果缓存，强制重新调用")

    p_dry = sub.add_parser("dryrun", help="干跑：不写库，但会推送钉钉")
    p_dry.add_argument("--slot", choices=["midday", "close"], required=True)
    p_dry.add_argument("--no-ai-cache", action="store_true", help="忽略 AI 结果缓存，强制重新调用")

    sub.add_parser("track", help="仅更新跟踪表（T+N 收益）")
    sub.add_parser("rebuild-index", help="仅重建 reports/index.html")

    # 兼容旧用法：python -m src.main --slot xxx
    p.add_argument("--slot", choices=["midday", "close"], default=None)

    args = p.parse_args()
    cmd = args.cmd or ("run" if args.slot else None)

    if cmd in (None, "run"):
        if not args.slot:
            p.error("--slot is required")
        return run(args.slot, dryrun=False, no_ai_cache=getattr(args, "no_ai_cache", False))
    if cmd == "dryrun":
        return run(args.slot, dryrun=True, no_ai_cache=getattr(args, "no_ai_cache", False))
    if cmd == "track":
        settings = load_settings()
        setup_logging(settings.log_level, settings.log_dir)
        repo = Repository(settings.sqlite_path, settings.data_dir)
        cache = KlineCache(settings.klines_cache_dir) if settings.klines_cache_enabled else None
        def _fetch(code: str, days: int = 80):
            return fetch_klines_concurrent([code], days=days, cache=cache, max_workers=1).get(code)
        n = update_pick_returns(repo, _fetch)
        logger.info("跟踪表更新 %d 条", n)
        return 0
    if cmd == "rebuild-index":
        settings = load_settings()
        setup_logging(settings.log_level, settings.log_dir)
        repo = Repository(settings.sqlite_path, settings.data_dir)
        runs = repo.list_reports(limit=1000)
        out = write_index(settings.reports_dir, runs)
        logger.info("已重建 %s", out)
        return 0
    p.error(f"未知命令: {cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
