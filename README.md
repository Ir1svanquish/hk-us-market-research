# 港美股研究报告工具

[English](README_EN.md)

一个用于整理港股和美股市场信息、生成每日研究报告的 Python 项目。它会汇总行情、公司公告、宏观数据和新闻，并输出 Markdown、HTML 或 PDF 报告；需要时也可以把报告发送到常用通知渠道。

## 主要功能

- 港股数据：长桥行情、HKEX 公告、董事会日历
- 美股数据：Yahoo Finance、Finnhub、SEC EDGAR
- 宏观事件：Nasdaq 美国经济日历、BLS 日程，以及 CPI、PPI、就业和央行事件
- 研究内容：技术结构、量价、基本面、板块轮动、新闻与事件风险
- 结构化分析：事件上下文、机会评分、交易卡、信号置信度和跨交易日状态
- 报告输出：Markdown、HTML、PDF
- 报告配送：Telegram、邮件、企业微信、飞书、Discord、Slack 等渠道

## 环境要求

- Python 3.10 或更高版本
- Linux 或 macOS
- PDF 输出需要安装 WeasyPrint 的系统依赖，具体见 [官方安装说明](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#linux)

## 安装

```bash
git clone https://github.com/Ir1svanquish/hk-us-market-research.git
cd hk-us-market-research

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 配置

港股和美股使用独立的环境文件：

```bash
cp .env.hk.example .env.hk
cp .env.us.example .env.us
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

完整示例见：

- `.env.example`
- `.env.hk.example`
- `.env.us.example`
- `.env.notifications.example`

这些文件只提供字段示例。请把真实凭据保存在本地环境文件中，不要提交到仓库。

## 运行

只生成基础报告，不发送通知：

```bash
cp .env.hk .env
python main.py --no-notify
```

按市场运行完整流程：

```bash
./run_hk_once.sh
./run_us_once.sh
```

运行脚本会先生成基础分析，再生成结构化报告。同一交易日重复执行时，会通过本地运行标记避免重复处理基础链路。

## 报告配送

系统会向所有已完成配置的渠道发送报告。目前支持：

- Telegram、邮件
- 企业微信、飞书、钉钉机器人
- Discord、Slack
- Pushover、PushPlus、Server酱3
- AstrBot、自定义 Webhook

渠道字段见 `.env.notifications.example`。只需要把实际使用的配置复制到 `.env.hk` 或 `.env.us`。

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
├── run_hk_once.sh       # 港股完整流程
└── run_us_once.sh       # 美股完整流程
```

## 说明

本项目生成的内容用于信息整理和研究，不构成投资建议。第三方数据源可能存在延迟、限流或字段调整，正式使用前请核对数据口径和授权条款。

## License

[MIT](LICENSE)
