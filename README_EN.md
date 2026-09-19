<div align="center">

# HK & US Market Research Reports

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> Collects market data, filings, macro releases, and news for Hong Kong and U.S. stocks, then builds daily market reviews and stock research reports with optional multi-channel delivery.

[**Features**](#-features) · [**Quick Start**](#-quick-start) · [**Report Output**](#-report-output) · [**Configuration**](#-configuration)

[简体中文](README.md) | English

</div>

Built on [daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis), with changes for a Hong Kong and U.S. daily research workflow.

## ✨ Features

| Capability | Details |
| --- | --- |
| Daily research reports | Market state, prevailing themes, stock conclusions, scores, trends, risk alerts, and catalysts |
| Hong Kong research | Longbridge quotes, HKEX filings, board calendars, southbound flows, and HKD liquidity |
| U.S. research | Yahoo Finance, Finnhub, SEC EDGAR, earnings windows, and company guidance |
| Macro events | Nasdaq U.S. economic calendar, BLS schedules, CPI, PPI, labor, and central-bank events |
| Opportunity scoring | Data completeness, signal confidence, opportunity strength, and execution readiness |
| Trade cards | Trigger, invalidation, stop, and target levels; incomplete evidence produces a watch-only conclusion |
| Social sentiment | Optional Reddit, X, and Polymarket sentiment for U.S. stocks |
| Historical validation | Tracks Top 5 signals over 1/5/20 days, favorable/adverse movement, and target/stop outcomes |
| Report output | Markdown, HTML, PDF, and short mobile-friendly briefs |
| Automated delivery | Local scheduling with Telegram, email, WeCom, Feishu, DingTalk, Discord, Slack, and other channels |

### Data and services

| Type | Supported |
| --- | --- |
| Models | OpenAI-compatible providers, DeepSeek, Qwen, Claude, Gemini, Ollama, and LiteLLM multi-channel routing |
| Market data | Longbridge, YFinance, Finnhub, AkShare, Tushare, Pytdx, Baostock, TickFlow |
| Official sources | HKEX, SEC EDGAR, Nasdaq, BLS |
| News search | Anspire, SerpAPI, Tavily, Bocha, Brave, MiniMax, SearXNG |
| Social sentiment | Reddit, X, and Polymarket for U.S. stocks (optional) |
| Delivery | Telegram, email, WeCom, Feishu, DingTalk, Discord, Slack, Pushover, PushPlus, ServerChan 3, AstrBot, custom webhooks |

## 🚀 Quick Start

### 1. Install

```bash
git clone https://github.com/Ir1svanquish/hk-us-market-research.git
cd hk-us-market-research

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Windows PowerShell:

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.hk.example .env.hk
cp .env.us.example .env.us
```

Windows PowerShell:

```powershell
Copy-Item .env.hk.example .env.hk
Copy-Item .env.us.example .env.us
```

### 3. Run

Build a Hong Kong formal report without sending notifications:

```bash
cp .env.hk .env
ANALYSIS_STAGE_ONLY=true python main.py --no-notify
python -m reporting.daily_report --market hk --env-file .env.hk --dry-run
```

For U.S. stocks, replace `hk` and `.env.hk` with `us` and `.env.us`. Linux servers can also run the complete workflow directly:

```bash
./run_hk_once.sh
./run_us_once.sh
```

Built-in scheduler:

```bash
python main.py --schedule
```

The Python application runs on Windows, Linux, and macOS. The two `run_*_once.sh` scripts depend on Bash, `flock`, and GNU utilities and are intended mainly for Linux. Other platforms can run the Python commands directly or use the operating system scheduler.

PDF output uses WeasyPrint; see the [official installation guide](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html).

## 📱 Report Output

### Daily brief

```text
HK/US Market Review and Opportunities | Date | Market State

Market State: range-bound / improving / risk-off
Main Theme: the session's dominant theme and key tension
Top Opportunities: score, data completeness, signal confidence
Trade Conditions: trigger, invalidation, stop, target
Event Watch: earnings windows, macro calendar, official filings
Risk Notes: stock risks, market risks, and data gaps
```

### Stock research

Each stock receives a core conclusion, suggested action, score, and trend, together with technical structure, price and volume, fundamentals, recent developments, risk alerts, and catalysts. U.S. reports also include Reddit, X, and Polymarket summaries when social sentiment is enabled.

### Full report

The full PDF includes the market review, opportunity ranking, stock research, event checks, and trade cards. Messaging channels receive a concise brief, while email and attachments retain the complete report.

## ⚙️ Configuration

Common settings:

| Variable | Purpose |
| --- | --- |
| `STOCK_LIST` | Analysis-stage stock universe, comma-separated |
| `REPORT_HK_SYMBOLS` / `REPORT_US_SYMBOLS` | Hong Kong and U.S. formal-report universes |
| `LLM_CHANNELS` / `LITELLM_MODEL` | Model channels and model name |
| `LONGBRIDGE_APP_KEY` / `LONGBRIDGE_APP_SECRET` / `LONGBRIDGE_ACCESS_TOKEN` | Realtime and valuation fields for Hong Kong and U.S. stocks |
| `FINNHUB_API_KEYS` | U.S. earnings, estimates, and institutional data |
| `TAVILY_API_KEYS` / `SERPAPI_API_KEYS` | News search |
| `SOCIAL_SENTIMENT_API_KEY_FILE` | Local key file for U.S. social sentiment data |
| `SEC_USER_AGENT` | SEC EDGAR request identity |

Notification settings are listed in `.env.notifications.example`; configure only the channels you use.

## Tests

```bash
python -m pytest -q
```

## License

[MIT License](LICENSE)

## Disclaimer

This project is intended for research and information organization only. It is not investment advice. Third-party data may be delayed, rate-limited, or changed without notice; verify data definitions and licensing terms before production use.
