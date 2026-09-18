# 港美股研究报告工具

[English](README_EN.md)

一个面向港股和美股的自动化研究与报告项目。它会把行情、公司公告、宏观数据和新闻整理成每日复盘、个股决策看板和机会报告，并按需输出 Markdown、HTML、PDF 或推送消息。

## 项目定位

本项目基于 [daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis) 的分析与通知能力开发。原项目覆盖更多市场，并提供完整的 Web、桌面端和 API 工作台；本仓库把范围收敛到港股和美股，重点放在研究报告的深度、官方来源核验和稳定交付。

主要差异：

- 只保留港股和美股链路，减少与目标市场无关的配置和运行负担
- 港股重点核验 HKEX 公告、董事会日历、南向资金和港元流动性
- 美股重点核验 SEC 文件、财报窗口、公司指引和美国宏观事件
- 在基础决策看板之上增加机会评分、数据完整度、信号置信度和标准化交易卡
- 记录跨交易日状态，并对历史 Top 5 信号做 1/5/20 日验证
- 将简短推送与完整研究报告分开，方便在手机上先看结论、需要时再读 PDF

## 数据与研究范围

- 港股数据：长桥行情、HKEX 公告、董事会日历
- 美股数据：Yahoo Finance、Finnhub、SEC EDGAR
- 宏观事件：Nasdaq 美国经济日历、BLS 日程，以及 CPI、PPI、就业和央行事件
- 市场分析：指数、板块轮动、市场状态、主线方向和次日观察重点
- 个股分析：技术结构、量价、基本面、新闻、风险警报和催化因素
- 结构化研究：事件闸门、机会评分、交易卡、信号置信度和跨交易日状态
- 报告输出：Markdown、HTML、PDF

## 报告内容与推送效果

### 决策看板

每只股票会给出核心结论、操作建议、评分和趋势，并整理关键点位、风险警报、利好催化和最新动态。报告不是简单罗列新闻，而是把数据和事件放到同一个决策框架中。

### 港美股复盘

报告会概括当日市场状态、主线方向、指数与板块表现，并列出需要继续跟踪的宏观事件和公司事件。港股和美股使用各自的数据源与市场规则，不共用一套泛化模板。

### 机会评分与交易卡

结构化报告会显示数据完整度、信号置信度和执行成熟度。满足条件的标的会给出触发条件、失效条件、止损和目标位；证据不足或信号冲突时，只保留观察结论。

### 推送摘要

不同渠道会收到适合其阅读方式的内容：即时通讯渠道以简报和关键风险为主，邮件和附件保留完整报告。典型内容结构如下：

```text
港股/美股复盘及机会日报｜日期｜市场状态

市场状态 / 当前主线 / 今日策略
重点机会 / 机会评分 / 数据完整度 / 信号置信度
触发条件 / 失效条件 / 止损 / 目标位
风险警报 / 利好催化 / 最新动态
未来宏观事件 / 财报窗口 / 官方公告核验
```

支持的推送渠道：

- Telegram、邮件
- 企业微信、飞书、钉钉机器人
- Discord、Slack
- Pushover、PushPlus、Server酱3
- AstrBot、自定义 Webhook

渠道字段见 `.env.notifications.example`。系统只会使用已经完成配置的渠道。

## 运行环境

- Python 3.10 或更高版本
- Python 主程序可在 Windows、Linux 和 macOS 上运行
- `run_hk_once.sh`、`run_us_once.sh` 使用 Bash、`flock` 和 GNU 工具，主要面向 Linux 服务器
- Windows 可以使用 PowerShell、任务计划程序或直接运行 Python 命令
- macOS 可以直接运行 Python 主程序；如需使用 Bash 自动化脚本，需要补齐 GNU 工具兼容环境
- PDF 输出依赖 WeasyPrint，系统依赖安装方式见 [官方文档](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html)

## 安装

### Linux / macOS

```bash
git clone https://github.com/Ir1svanquish/hk-us-market-research.git
cd hk-us-market-research

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### Windows PowerShell

```powershell
git clone https://github.com/Ir1svanquish/hk-us-market-research.git
Set-Location hk-us-market-research

py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## 配置

港股和美股使用独立的环境文件。

Linux / macOS：

```bash
cp .env.hk.example .env.hk
cp .env.us.example .env.us
```

Windows PowerShell：

```powershell
Copy-Item .env.hk.example .env.hk
Copy-Item .env.us.example .env.us
```

常用配置项：

| 配置 | 用途 |
| --- | --- |
| `STOCK_LIST` | 股票池，多个代码用逗号分隔 |
| `LONGBRIDGE_APP_KEY` / `LONGBRIDGE_APP_SECRET` / `LONGBRIDGE_ACCESS_TOKEN` | 长桥行情 |
| `FINNHUB_API_KEYS` | 美股财报和一致预期 |
| `TUSHARE_TOKEN` | 港股基本面备用数据 |
| `LLM_CHANNELS` / `LITELLM_MODEL` | 模型渠道和模型名称 |
| `TAVILY_API_KEYS` / `SERPAPI_API_KEYS` | 新闻搜索 |
| `V2_HK_SYMBOLS` / `V2_US_SYMBOLS` | 结构化报告股票池 |
| `V2_SEC_USER_AGENT` | SEC EDGAR 请求标识 |

完整示例见 `.env.example`、`.env.hk.example`、`.env.us.example` 和 `.env.notifications.example`。这些文件只提供字段示例，请把真实凭据保存在本地环境文件中，不要提交到仓库。

## 运行

基础分析在三个平台上都可以直接运行。

Linux / macOS：

```bash
cp .env.hk .env
python main.py --no-notify
```

Windows PowerShell：

```powershell
Copy-Item .env.hk .env
python main.py --no-notify
```

内置定时模式：

```bash
python main.py --schedule
```

Linux 服务器可以直接运行完整港股或美股流程：

```bash
./run_hk_once.sh
./run_us_once.sh
```

这两个脚本会依次生成基础分析和结构化报告，并使用本地运行标记避免同一交易日重复处理。Windows 或 macOS 也可以在基础报告生成后直接调用结构化报告模块：

```bash
python -m v2.shadow_delivery --market hk --date YYYY-MM-DD --env-file .env.hk --delivery-mode v2
```

美股运行时把 `hk` 和 `.env.hk` 分别改为 `us` 和 `.env.us`。

## 测试

```bash
python -m pytest tests -q
python -m pytest v2/tests -q
```

## 项目结构

```text
.
├── main.py              # 基础分析入口
├── data_provider/       # 行情和基本面数据源
├── src/                 # 分析、报告与通知
├── strategies/          # 可配置研究策略
├── templates/           # 报告模板
├── v2/                  # 事件、评分和交易卡
├── tests/               # 基础链路测试
├── run_hk_once.sh       # 港股完整流程（Linux）
└── run_us_once.sh       # 美股完整流程（Linux）
```

## 说明

本项目生成的内容用于信息整理和研究，不构成投资建议。第三方数据源可能存在延迟、限流或字段调整，正式使用前请核对数据口径和授权条款。

## License

[MIT](LICENSE)
