"""集中配置加载与校验。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from dotenv import load_dotenv

load_dotenv()


def _get(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _get_bool(key: str, default: bool = False) -> bool:
    val = _get(key, str(default)).lower()
    return val in ("1", "true", "yes", "y", "on")


def _get_int(key: str, default: int) -> int:
    try:
        return int(_get(key, str(default)))
    except ValueError:
        return default


def _get_float(key: str, default: float) -> float:
    try:
        return float(_get(key, str(default)))
    except ValueError:
        return default


def _get_list(key: str, default: str = "") -> List[str]:
    raw = _get(key, default)
    return [x.strip() for x in raw.split(",") if x.strip()]


@dataclass
class StrategyConfig:
    # 硬过滤
    risk_enabled: bool = True
    # 一票否决
    veto_stagnation_enabled: bool = True
    veto_stagnation_pct_20d: float = 30.0  # 20日累计涨幅阈值 (%)
    veto_stagnation_near_52w: float = 0.10  # 距 52 周高点小于该比例认为高位
    veto_stagnation_vol_ratio: float = 2.0  # 当日量/5日均量
    veto_stagnation_pct_today: float = 2.0  # 今日涨幅<该值为滞涨 (%)
    veto_stagnation_upper_shadow: float = 3.0  # 上影/close (%)
    veto_bias_enabled: bool = True
    veto_bias10: float = 15.0  # close 高于 MA10 超过 % 则否决
    veto_bias20: float = 20.0
    # 信号策略
    ma_enabled: bool = True
    macd_enabled: bool = True
    rsi_enabled: bool = True
    breakout_enabled: bool = True
    pattern_enabled: bool = True
    volume_enabled: bool = True
    pct_enabled: bool = True
    liquidity_enabled: bool = True
    # 阈值
    volume_ratio_min: float = 1.2
    pct_min: float = -3.0
    pct_max: float = 7.0
    turnover_floor: float = 2e8
    amplitude_cap: float = 15.0
    min_signals: int = 2


@dataclass
class Settings:
    # DeepSeek
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    deepseek_timeout: int = 30
    deepseek_max_retry: int = 3

    # 钉钉
    dingtalk_webhook: str = ""
    dingtalk_secret: str = ""
    dingtalk_at_mobiles: List[str] = field(default_factory=list)
    dingtalk_at_all: bool = False

    # 选股范围
    top_n_turnover: int = 100
    top_n_hot: int = 100
    exclude_prefixes: List[str] = field(default_factory=lambda: ["688", "920", "83", "87", "88"])
    exclude_name_keywords: List[str] = field(
        default_factory=lambda: ["ST", "*ST", "退"]
    )

    # 策略
    strategy: StrategyConfig = field(default_factory=StrategyConfig)

    # 决策
    focus_score: int = 80
    top_k_focus: int = 10
    ai_max_candidates: int = 20
    ai_daily_budget: int = 0  # 0=不限
    push_top_n: int = 5  # 推送 AI 评分前 N 名（与重点票合并去重）

    # 并发 / 缓存
    klines_workers: int = 6
    klines_days: int = 250
    klines_cache_dir: Path = Path("./data/klines")
    klines_cache_enabled: bool = True

    # 行业 / 大盘 / 一字板
    industry_diversify_enabled: bool = True
    industry_max_per_industry: int = 2
    industry_cache_path: Path = Path("./data/industry_map.json")
    industry_cache_ttl_days: int = 7
    market_filter_enabled: bool = True
    market_filter_min_score: int = 90  # 大盘不利时仅推 score>=该值
    universe_min_size: int = 50  # 选股池<该值则中止 (熝断)
    yiziban_filter_enabled: bool = True

    # 跟踪 / 评估
    tracking_enabled: bool = True
    tracking_lookback_days: int = 30

    # 报告 / HTML
    report_host: str = ""  # 例如 http://example.com/reports
    reports_dir: Path = Path("./reports")
    report_kline_chart: bool = True  # 报告中嵌入 ECharts 小图

    # SMTP 备用通道
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_sender: str = ""
    smtp_recipients: List[str] = field(default_factory=list)
    smtp_use_ssl: bool = True

    # 存储
    data_dir: Path = Path("./data")
    sqlite_path: Path = Path("./data/dailystock.db")

    # 日志
    log_level: str = "INFO"
    log_dir: Path = Path("./logs")

    def validate(self) -> List[str]:
        errors: List[str] = []
        if not self.deepseek_api_key:
            errors.append("DEEPSEEK_API_KEY 未配置")
        if not self.dingtalk_webhook:
            errors.append("DINGTALK_WEBHOOK 未配置")
        if not self.report_host:
            errors.append("REPORT_HOST 未配置（HTML 报告外链域名）")
        return errors


def load_settings() -> Settings:
    s = Settings(
        deepseek_api_key=_get("DEEPSEEK_API_KEY"),
        deepseek_base_url=_get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        deepseek_model=_get("DEEPSEEK_MODEL", "deepseek-chat"),
        deepseek_timeout=_get_int("DEEPSEEK_TIMEOUT", 30),
        deepseek_max_retry=_get_int("DEEPSEEK_MAX_RETRY", 3),
        dingtalk_webhook=_get("DINGTALK_WEBHOOK"),
        dingtalk_secret=_get("DINGTALK_SECRET"),
        dingtalk_at_mobiles=_get_list("DINGTALK_AT_MOBILES"),
        dingtalk_at_all=_get_bool("DINGTALK_AT_ALL", False),
        top_n_turnover=_get_int("TOP_N_TURNOVER", _get_int("TOP_N", 100)),
        top_n_hot=_get_int("TOP_N_HOT", 100),
        exclude_prefixes=_get_list("EXCLUDE_PREFIXES", "688,920,83,87,88"),
        exclude_name_keywords=_get_list("EXCLUDE_NAME_KEYWORDS", "ST,*ST,退"),
        strategy=StrategyConfig(
            risk_enabled=_get_bool("STRAT_RISK_ENABLED", True),
            veto_stagnation_enabled=_get_bool("STRAT_VETO_STAGNATION_ENABLED", True),
            veto_stagnation_pct_20d=_get_float("VETO_STAGNATION_PCT_20D", 30.0),
            veto_stagnation_near_52w=_get_float("VETO_STAGNATION_NEAR_52W", 0.10),
            veto_stagnation_vol_ratio=_get_float("VETO_STAGNATION_VOL_RATIO", 2.0),
            veto_stagnation_pct_today=_get_float("VETO_STAGNATION_PCT_TODAY", 2.0),
            veto_stagnation_upper_shadow=_get_float("VETO_STAGNATION_UPPER_SHADOW", 3.0),
            veto_bias_enabled=_get_bool("STRAT_VETO_BIAS_ENABLED", True),
            veto_bias10=_get_float("VETO_BIAS10", 15.0),
            veto_bias20=_get_float("VETO_BIAS20", 20.0),
            ma_enabled=_get_bool("STRAT_MA_ENABLED", True),
            macd_enabled=_get_bool("STRAT_MACD_ENABLED", True),
            rsi_enabled=_get_bool("STRAT_RSI_ENABLED", True),
            breakout_enabled=_get_bool("STRAT_BREAKOUT_ENABLED", True),
            pattern_enabled=_get_bool("STRAT_PATTERN_ENABLED", True),
            volume_enabled=_get_bool("STRAT_VOLUME_ENABLED", True),
            pct_enabled=_get_bool("STRAT_PCT_ENABLED", True),
            liquidity_enabled=_get_bool("STRAT_LIQUIDITY_ENABLED", True),
            volume_ratio_min=_get_float("VOLUME_RATIO_MIN", 1.2),
            pct_min=_get_float("PCT_MIN", -3.0),
            pct_max=_get_float("PCT_MAX", 7.0),
            turnover_floor=_get_float("TURNOVER_FLOOR", 2e8),
            amplitude_cap=_get_float("AMPLITUDE_CAP", 15.0),
            min_signals=_get_int("MIN_SIGNALS", 2),
        ),
        focus_score=_get_int("FOCUS_SCORE", 80),
        top_k_focus=_get_int("TOP_K_FOCUS", 10),
        ai_max_candidates=_get_int("AI_MAX_CANDIDATES", 20),
        ai_daily_budget=_get_int("AI_DAILY_BUDGET", 0),
        push_top_n=_get_int("PUSH_TOP_N", 5),
        klines_workers=_get_int("KLINES_WORKERS", 6),
        klines_days=_get_int("KLINES_DAYS", 250),
        klines_cache_dir=Path(_get("KLINES_CACHE_DIR", "./data/klines")),
        klines_cache_enabled=_get_bool("KLINES_CACHE_ENABLED", True),
        industry_diversify_enabled=_get_bool("INDUSTRY_DIVERSIFY_ENABLED", True),
        industry_max_per_industry=_get_int("INDUSTRY_MAX_PER_INDUSTRY", 2),
        industry_cache_path=Path(_get("INDUSTRY_CACHE_PATH", "./data/industry_map.json")),
        industry_cache_ttl_days=_get_int("INDUSTRY_CACHE_TTL_DAYS", 7),
        market_filter_enabled=_get_bool("MARKET_FILTER_ENABLED", True),
        market_filter_min_score=_get_int("MARKET_FILTER_MIN_SCORE", 90),
        universe_min_size=_get_int("UNIVERSE_MIN_SIZE", 50),
        yiziban_filter_enabled=_get_bool("YIZIBAN_FILTER_ENABLED", True),
        tracking_enabled=_get_bool("TRACKING_ENABLED", True),
        tracking_lookback_days=_get_int("TRACKING_LOOKBACK_DAYS", 30),
        report_host=_get("REPORT_HOST", "").rstrip("/"),
        reports_dir=Path(_get("REPORTS_DIR", "./reports")),
        report_kline_chart=_get_bool("REPORT_KLINE_CHART", True),
        smtp_host=_get("SMTP_HOST", ""),
        smtp_port=_get_int("SMTP_PORT", 465),
        smtp_user=_get("SMTP_USER", ""),
        smtp_password=_get("SMTP_PASSWORD", ""),
        smtp_sender=_get("SMTP_SENDER", ""),
        smtp_recipients=_get_list("SMTP_RECIPIENTS"),
        smtp_use_ssl=_get_bool("SMTP_USE_SSL", True),
        data_dir=Path(_get("DATA_DIR", "./data")),
        sqlite_path=Path(_get("SQLITE_PATH", "./data/dailystock.db")),
        log_level=_get("LOG_LEVEL", "INFO"),
        log_dir=Path(_get("LOG_DIR", "./logs")),
    )
    s.data_dir.mkdir(parents=True, exist_ok=True)
    s.log_dir.mkdir(parents=True, exist_ok=True)
    s.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    s.reports_dir.mkdir(parents=True, exist_ok=True)
    if s.klines_cache_enabled:
        s.klines_cache_dir.mkdir(parents=True, exist_ok=True)
    return s
