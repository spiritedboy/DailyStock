# DailyStock

每个交易日中午 12:00 与下午 16:00 自动执行：拉取 A 股**成交额前 100 名 ∪ 同花顺热榜前 100 名**（去重、排除 688 与 ST），跑常用策略筛选（含**一票否决**），调用 DeepSeek 判定后选出 **AI 评分前 5 ∪ 重点票** 生成 HTML 报告（按月分目录），钉钉只推一条带 URL 的文本消息。HTML 中会含上次推送股票的跌幅回看。

## 功能模块
- 数据获取：AKShare（`stock_zh_a_spot_em` 实时全市场 + `stock_zh_a_hist` 日线）
- 策略筛选：5 个默认策略（趋势/量能/涨跌幅/流动性/风险）可单独启停
- AI 判定：DeepSeek Chat，输出严格 JSON `{score, allow, reason}`
- 消息推送：钉钉自定义机器人 Webhook，markdown（汇总）+ actionCard（重点单发），text 回退
- 持久化：SQLite（`runs`/`picks`/`notifications`）+ 每日 CSV 快照
- 调度：Linux Cron / systemd timer，支持 `--slot midday|close`

## 目录
```
src/
  config.py          # .env 配置加载与校验
  logging_setup.py   # 日志（控制台 + 滚动文件）
  models.py          # StockSnapshot/StrategyResult/AiDecision/StockEvaluation
  data/fetcher.py    # 行情与日线
  strategy/
    indicators.py    # MA / 均量
    rules.py         # 5 个默认策略
    pipeline.py      # 策略管线
  ai/deepseek_client.py
  decision/ranker.py
  notify/
    dingtalk.py      # 钉钉发送（含加签）
    templates.py     # 消息模板
  storage/repository.py
  main.py            # 入口
deploy/cron.daily_stock
```

## 安装
```bash
cd /home/yyf/DailyStock
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env 填入 DEEPSEEK_API_KEY、DINGTALK_WEBHOOK（可选 DINGTALK_SECRET）
```

## 手动执行
```bash
source .venv/bin/activate
python -m src.main --slot midday   # 中午 12:00
python -m src.main --slot close    # 下午 16:00
```

## 配置定时任务
```bash
crontab -e
# 复制 deploy/cron.daily_stock 中的两行
# 确认服务器时区：timedatectl | grep "Time zone"
# 若不是 Asia/Shanghai：sudo timedatectl set-timezone Asia/Shanghai
```

## 铉钉消息格式（底层支持）
基于官方文档 https://open.dingtalk.com/document/development/robot-message-type

| 用途 | msgtype | 关键字段 |
| --- | --- | --- |
| 实际推送 | `text` | `text.content`（内容为报告 URL） |
| 可选 | `markdown` | `markdown.title`, `markdown.text` |
| 可选 | `actionCard` | `actionCard.title`, `actionCard.text`, `singleTitle`, `singleURL` |
| 可选 | `feedCard` | `feedCard.links[]` |

均支持 `at.atMobiles` / `at.isAtAll`；机器人开启加签时通过 `DINGTALK_SECRET` 自动加签。

## 选股范围
- **成交额 Top N**（`TOP_N_TURNOVER`，默认 100）∪ **同花顺热榜 Top N**（`TOP_N_HOT`，默认 100）
- 按代码去重后，**逐只**过策略。钉钉表格中会展示来源（`turnover` / `ths_hot`）
- 数据源：AKShare `stock_zh_a_spot_em` + `stock_hot_rank_wc`（问财/同花顺热榜）

## 默认策略（按优先级：硬过滤 → 一票否决 → 信号）

### 1）硬过滤
名称含 ST/*ST/退 或价格异常 → 直接淘汰。

### 2）一票否决（命中任一条直接出局，不再计信号）

| 策略 | 逻辑 | 阈值变量 |
| --- | --- | --- |
| VETO_STAGNATION 高位滞涨（防主力派发） | “高位” 且 “放量” 且 “滞涨”同时成立。高位=20日累计涨幅>`VETO_STAGNATION_PCT_20D` 或 距52周高点 < `VETO_STAGNATION_NEAR_52W`；放量=当日量/5日均量>=`VETO_STAGNATION_VOL_RATIO`；滞涨=今日涨幅<`VETO_STAGNATION_PCT_TODAY` 或 (high-close)/close>`VETO_STAGNATION_UPPER_SHADOW`% | `STRAT_VETO_STAGNATION_ENABLED` |
| VETO_BIAS 乖离率过大（防短线回调踩踏） | close 高于 MA10 超 `VETO_BIAS10`% 或 高于 MA20 超 `VETO_BIAS20`% | `STRAT_VETO_BIAS_ENABLED` |

### 3）信号策略（每命中 1 票，hits ≥ `MIN_SIGNALS` 进候选池）

| 策略 | 说明 | 开关 |
| --- | --- | --- |
| MA_CROSS | MA5 金叉 MA10，或多头排列 (MA5>MA10>MA20) 且 close>MA20 | `STRAT_MA_ENABLED` |
| MACD | DIF>DEA 且柱状转正 / 持续多头 | `STRAT_MACD_ENABLED` |
| RSI | RSI14 在 40-70 健康区，或 <30 超卖反弹且当日上涨 | `STRAT_RSI_ENABLED` |
| BREAKOUT | 收盘突破近 20 日高点 | `STRAT_BREAKOUT_ENABLED` |
| PATTERN | 双底形态且未出现头肩顶 | `STRAT_PATTERN_ENABLED` |
| VOLUME | 当日量 ≥ 5 日均量 × `VOLUME_RATIO_MIN` | `STRAT_VOLUME_ENABLED` |
| PCT_RANGE | 涨跌幅 ∈ [`PCT_MIN`, `PCT_MAX`] | `STRAT_PCT_ENABLED` |
| LIQUIDITY | 成交额 ≥ `TURNOVER_FLOOR` 且振幅 ≤ `AMPLITUDE_CAP` | `STRAT_LIQUIDITY_ENABLED` |

所有阈值可在 `.env` 内调整；候选池逻辑为：**硬过滤 → 一票否决 → 信号≥2**，候选池逐只调用 DeepSeek。

## 决策与推送
- 候选池 = 硬过滤通过 且 未被否决 且 信号命中数 ≥ `MIN_SIGNALS`（默认 2）
- 重点 = AI `allow=true` 且 `score ≥ FOCUS_SCORE`（默认 80）
- **推送名单 = AI 评分前 `PUSH_TOP_N`（默认 5）∪ 重点票**，去重后按评分降序
- 同一 (run_date, run_slot) 已发送成功的消息会自动去重

## HTML 报告
文件路径：`reports/{YYYY-MM}/{MM-DD}-{noon|afternoon}.html`
- 中午场 `--slot midday` → `MM-DD-noon.html`
- 收盘场 `--slot close` → `MM-DD-afternoon.html`

外链 URL：`{REPORT_HOST}/{YYYY-MM}/{MM-DD}-{noon|afternoon}.html`。需提前用 Nginx/HTTP 服务器将 `REPORTS_DIR` 映射到 `REPORT_HOST`。例：
```nginx
location /reports/ {
  alias /home/yyf/DailyStock/reports/;
  autoindex on;
}
```

HTML 包含：
- 运行统计：样本量 / 一票否决数 / 候选池 / AI 调用-允许 / 推送数
- 本次推送表：代码名称、来源、价格、AI 评分与判定、命中信号、AI 理由 + 可展开的策略明细
- 上次推送回看表：推送时价 vs 当前价、区间涨跌幅、胜率、平均涨跌幅

## 钉钉推送
只发送一条 `text` 消息，内容包含报告标题与 URL，例：
```
DailyStock 选股报告 - 2026-05-09 下午 16:00
推送 5 只 (重点 1 / TopAI 5)
http://your.host/reports/2026-05/05-09-afternoon.html
```

## 钉钉发送能力（底层保留）
[`DingTalkClient`](src/notify/dingtalk.py) 仍提供 `text / markdown / actionCard / link / feedCard`及加签能力，需要时可复用。

## DeepSeek 输入内容
对每只候选股，提示词会包含：
- 基本信息：代码、名称、**来源**(turnover/ths_hot)、最新价、涨跌幅、成交量/额、振幅、开/高/低/收
- 技术指标：MA5/10/20/60、**BIAS10/20**、**20日累计涨幅**、MACD(DIF/DEA/HIST)、RSI14、近 20 日高/低、**52周高点**、量比
- 策略命中：命中信号列表、未命中列表、明细

返回统一为 JSON `{score, allow, reason}`。

## 排障
- 行情为空：交易时段外或 AKShare 限流，可稍后重跑或检查网络
- DeepSeek 失败：会标记 `ok=false` 不进入重点；查看 `logs/dailystock.log`
- 钉钉错误码：`310000`(签名错误)、`130101`(频率限制)、`410100`(关键字未命中) 等
