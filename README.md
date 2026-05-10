# DailyStock

每个交易日中午 12:00 与下午 16:00 自动执行：拉取 A 股**成交额前 100 名 ∪ 同花顺热榜前 100 名**，跑常用策略筛选（含**一票否决**），调用 DeepSeek 判定后选出 **AI 评分前 5 ∪ 重点票** 生成 HTML 报告（按月分目录），钉钉只推一条带 URL 的文本消息。HTML 中含上次推送股票的回看、历史表现、策略归因、AI 校准与 60 日 K 线小图。

## 功能模块
- **数据获取**：AKShare 双源回退（实时行情 东财↔新浪、日线 东财↔新浪），日线本地 CSV 缓存 + 多线程拉取；热榜 6 源依次降级
- **大盘环境**：上证指数 vs MA20，弱势时仅推送 AI 高分票
- **策略筛选**：5+ 信号策略 + 2 个一票否决，可单独启停
- **AI 判定**：DeepSeek Chat（交易员人格提示词），结果按 `prompt_ver+run_date+code` 缓存（`ai_cache` 表，提示词变更自动失效）+ 调用预算（`AI_DAILY_BUDGET`）+ `--no-ai-cache` 强制重算
- **推送过滤**：剔除一字板（涨停且开=高=低=收，无法买入）；同一行业最多 N 只
- **HTML 报告**：手机端卡片式响应式 UI、按月分目录、ECharts 小 K 线、`reports/index.html` 总索引 + 月度索引
- **跟踪表现**：`pick_returns` 表记录推送票 T+1/3/5/10/20 收益；报告内嵌策略归因 & AI 评分校准
- **通知**：钉钉只发 URL 文本；钉钉异常自动邮件兜底（SMTP）
- **可观测**：`logs/metrics.jsonl` 结构化日志（每次运行一行）
- **CLI**：`run` / `dryrun` / `track` / `rebuild-index` 子命令
- **CI**：GitHub Actions（`compileall` + `pytest`）

## 目录
```
src/
  config.py              # .env 配置加载与校验
  logging_setup.py       # 日志（控制台 + 滚动文件）
  metrics.py             # 结构化指标 JSONL 输出
  models.py              # 数据模型
  data/
    fetcher.py           # 行情/日线（含并发 + 重试）
    cache.py             # K 线本地 CSV 缓存
    industry.py          # 行业映射（东方财富板块成分，7 天缓存）
    market.py            # 大盘环境评估（上证 vs MA20）
  strategy/
    indicators.py        # MA / 均量 / MACD / RSI / 形态
    rules.py             # 信号 + 一票否决
    pipeline.py          # 并发管线
    filters.py           # 一字板剔除 / 行业去集中
  ai/deepseek_client.py  # 缓存 + 预算
  decision/ranker.py
  notify/
    dingtalk.py          # 钉钉自定义机器人
    email_client.py      # SMTP 兜底
    templates.py
  storage/repository.py  # SQLite + CSV
  report/html_renderer.py# HTML/index 渲染
  tracking/tracker.py    # T+N 收益、策略归因、AI 校准
  main.py                # 入口（含子命令）
tests/                   # pytest 单测
.github/workflows/ci.yml
deploy/cron.daily_stock
```

## 安装
```bash
cd /home/yyf/DailyStock
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env：DEEPSEEK_API_KEY、DINGTALK_WEBHOOK、REPORT_HOST 等
```

## CLI 子命令速查

| 命令 | 何时用 | 副作用 |
| --- | --- | --- |
| `run --slot midday\|close` | **正式运行**（cron 自动调） | 写库 + 写报告 + 推钉钉 + 跟踪 |
| `dryrun --slot midday\|close` | **测试 / 调参 / 新环境验证** | 生成 HTML 到本地，**不写库、会推钉钉（标题带[DRYRUN]）、不更新跟踪表** |
| `track` | 单独补算历史推送的 T+N 收益 | 只更新 `pick_returns` 表 |
| `rebuild-index` | 改了 HTML 模板想重刷总索引 | 只重写 `reports/index.html` |

两个 `run` / `dryrun` 都支持 `--no-ai-cache`：忽略 `ai_cache` 表、强制重新调用 DeepSeek（调试新提示词时使用）。

```bash
source .venv/bin/activate

# 正常运行（默认）
python -m src.main run --slot midday   # 中午 12:00
python -m src.main run --slot close    # 下午 16:00

# 干跑（不写库、不推送，仅生成报告到本地）
python -m src.main dryrun --slot close

# 调试提示词：忽略缓存重算 AI
python -m src.main dryrun --slot close --no-ai-cache

# 仅更新跟踪表（补算 T+N 收益，可定时单独跑）
python -m src.main track

# 仅重建 reports/index.html 总索引
python -m src.main rebuild-index

# 兼容旧用法
python -m src.main --slot midday
```

### 典型场景：全新环境上线

#### 周末/非交易时段：装环境 + 干跑测试

```bash
# 1) 装依赖
cd /home/yyf/DailyStock
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt pytest
cp .env.example .env
# 编辑 .env：DEEPSEEK_API_KEY、DINGTALK_WEBHOOK、REPORT_HOST 等
# 关键项：
#   SPOT_SOURCE=auto           行情源东财→新浪自动回退（默认）
#   PUSH_TOP_N=5               推送条数
#   AI_DAILY_BUDGET=0          DeepSeek 每日调用上限（0=不限）
#   UNIVERSE_MIN_SIZE=50       样本不足直接中止；周末测试可临时改 0

# 2) 跑单元测试（不联网）
pytest -q

# 3) 干跑（看 reports/{YYYY-MM}/{MM-DD}-{noon|afternoon}.html 是否正常）
#    - 周末/盘前 fetch_universe 可能为空 → 临时把 .env 里 UNIVERSE_MIN_SIZE=0
#    - 调试新提示词时加 --no-ai-cache 强制重新调用 DeepSeek
python -m src.main dryrun --slot midday
python -m src.main dryrun --slot close --no-ai-cache

# 4) 验证钉钉通道（用最小参数避免误推太多）
#    临时把 .env：PUSH_TOP_N=1、TOP_N_TURNOVER=20、TOP_N_HOT=20
python -m src.main run --slot close   # 看钉钉是否收到 URL

# 5) 数据预热（首次会拉所有股票日线，比较慢；之后只增量）
#    K 线缓存写到 ./data/klines/*.csv
#    若日志报"行情源 em 失败"是正常现象，会自动切到新浪
python -m src.main dryrun --slot close
```

#### 部署 Web 服务器（让 HTML 能从外网访问）

```nginx
# /etc/nginx/sites-available/dailystock
server {
  listen 80;
  server_name your.host;

  location /reports/ {
    alias /home/yyf/DailyStock/reports/;
    autoindex on;
    add_header Cache-Control "no-cache";
  }
}
```
注意 `.env` 里的 `REPORT_HOST` 必须与 Nginx 暴露的前缀一致（例 `http://your.host/reports`），否则钉钉里的链接会 404。

#### 交易日：装 cron 自动跑

```bash
# 1) 把 .env 改回正式参数（UNIVERSE_MIN_SIZE=50、PUSH_TOP_N=5、TOP_N_*=100）

# 2) 装定时任务
crontab -e
# 复制 deploy/cron.daily_stock 三行：
#   12:00 run midday
#   16:00 run close
#   17:30 track（补 T+N 收益）

# 3) 验证时区
timedatectl | grep "Time zone"        # 必须 Asia/Shanghai
# 如不对：sudo timedatectl set-timezone Asia/Shanghai
```

#### 升级到新版本（已有部署）

```bash
cd /home/DailyStock
git pull
# requirements 若有变动：source .venv/bin/activate && pip install -r requirements.txt
# 数据库自动迁移（启动时会给 ai_cache 补 prompt_ver 列），无需手动 ALTER TABLE
# 想立刻看新提示词的效果：
python -m src.main dryrun --slot midday --no-ai-cache
```

### 日常维护

- **改了 HTML 模板/想刷新索引**：`python -m src.main rebuild-index`
- **手动补一次历史收益**（比如停了几天 cron）：`python -m src.main track`
- **临时调参不想污染数据库**：永远先 `dryrun` 看效果再 `run`
- **想重发当次钉钉**：删 `notifications` 表里对应行 → 再 `run`

> ⚠️ `dryrun` 不写库、不更新 `pick_returns`，所以**只用 dryrun 测试期间，跟踪表不会涨数据**。要正式 `run` 才会逐日累积。

## 测试
```bash
pip install pytest
pytest -q
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
- **成交额 Top N**（`TOP_N_TURNOVER`，默认 100）∪ **热榜 Top N**（`TOP_N_HOT`，默认 100）
- 按代码去重后，**逐只**过策略。钉钉/HTML 中会展示来源（`turnover` / `ths_hot`）
- 实时行情：`SPOT_SOURCE=auto` 时按 `stock_zh_a_spot_em`（东财）→ `stock_zh_a_spot`（新浪）顺序回退，每个源 3 次重试；可强制 `em` / `sina`
- 日线：`stock_zh_a_hist`（东财）→ `stock_zh_a_daily`（新浪，自动加 sh/sz/bj 前缀）双源回退
- 热榜：依次 `stock_hot_rank_em` → `_ths` → `_wc` → `stock_hot_up_em` → `stock_hot_search_baidu` → `stock_hot_rank_latest_em`，**自动跳过没有代码列的源**；全部失败仅 INFO 不报错
- 行业映射：`stock_board_industry_name_em` 单次拉取（不重试），失败写入 30 分钟失败标记避免反复重连

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
对每只候选股，提示词会包含（**仅传入已计算出有效数值的指标**，缺失项不会出现，避免诱导 AI 输出"指标缺失"）：
- 基本信息：代码、名称、**来源**(turnover/ths_hot)、最新价、涨跌幅、成交量/额、振幅、开/高/低/收
- 技术指标：MA5/10/20/60、**BIAS10/20**、**20日累计涨幅**、MACD(DIF/DEA/HIST)、RSI14、近 20 日高/低、**52周高点**、量比
- 策略命中：命中信号列表、未命中列表、明细

系统提示词为**经验交易员人格**（趋势位置/量价关系/动能与拐点/风险面/性价比 5 维度评分，硬性禁止"指标缺失/数据不足/无法判断"等推诿表述）。返回统一为 JSON `{score, allow, reason}`。

**缓存**：结果按 `(run_date, code, prompt_ver)` 缓存到 SQLite `ai_cache` 表，`prompt_ver = md5(SYSTEM_PROMPT)[:10]`。**提示词改动后旧缓存自动失效**，无需手动清理。临时调试可加 `--no-ai-cache`。

## 排障
- 行情为空：交易时段外或 AKShare 限流；`SPOT_SOURCE=auto` 时会自动回退到新浪，仍空则可稍后重跑或检查网络
- 日线 `RemoteDisconnected`：东财长连接不稳；fetcher 会自动切到新浪重试，无需手动干预
- 热榜日志 `热榜源 ... 命中但无代码列`：正常，自动跳过到下一源
- 行业映射卡住：30 分钟内只重试一次（`industry_map.fail` 标记），如要强制刷新删除 `data/industry_map.fail` 即可
- AI 报告反复出现旧理由：`prompt_ver` 已会自动让旧缓存失效；如有意外可 `--no-ai-cache` 重跑或 `sqlite3 data/dailystock.db "DELETE FROM ai_cache WHERE run_date='YYYY-MM-DD';"`
- DeepSeek 超时（`Read timed out`）：API 高负载或故障。缓解方案：(1) 改 `.env` `DEEPSEEK_TIMEOUT=60`（从 30 改到 60 秒）；(2) `AI_MAX_CANDIDATES=5`（降低并发，从 0 全量改为只调前 5 只候选，减轻 API 压力）；(3) 如果还是频繁超时说明 DeepSeek 那边确实有问题，建议降档次、稍后重跑或联系官方
- DeepSeek 失败：会标记 `ok=false` 不进入重点；查看 `logs/dailystock.log`
- 钉钉错误码：`310000`(签名错误)、`130101`(频率限制)、`410100`(关键字未命中) 等
