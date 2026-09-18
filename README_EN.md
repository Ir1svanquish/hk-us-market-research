<div align="center">

# HK & US Market Research Reports

> A Hong Kong and U.S. research-focused branch of [daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis), with additional work on official-source checks, opportunity scoring, trade cards, and report delivery.

[**Changes in this repository**](#-changes-in-this-repository) · [**Report output**](#-report-output) · [**Quick start**](#-quick-start) · [**Release scope**](#-current-release-scope)

[简体中文](README.md) | English

</div>

## ✨ Changes in this repository

The upstream project already provides market data, model routing, news search, decision dashboards, and multi-channel notifications. This README focuses on the Hong Kong and U.S. report changes made in this repository.

| Change | Details |
| --- | --- |
| Hong Kong market context | HKEX filings, board calendars, southbound flows, HKD liquidity, and related market variables |
| U.S. official sources | SEC EDGAR, earnings windows, company guidance, the Nasdaq economic calendar, and BLS schedules |
| Social sentiment | Optional Reddit, X, and Polymarket data for U.S. stocks, included in the analysis context and final report |
| Opportunity scoring | Opportunity strength, data completeness, signal confidence, and execution readiness in addition to the base score |
| Standardized trade cards | Trigger, invalidation, stop, and target levels; incomplete evidence produces a watch-only conclusion |
| Cross-session state | Carries events and signals into later sessions instead of rebuilding every decision from scratch |
| Historical validation | Tracks Top 5 signals over 1/5/20 days, including favorable/adverse movement and target/stop outcomes |
| Report delivery | Short briefs for messaging channels and full PDFs for email or attachments, with idempotent delivery |

Base model providers, market-data sources, search services, and notification channels follow the upstream implementation. See the [upstream README](https://github.com/ZhuLinsen/daily_stock_analysis/blob/main/README.md) for the broader feature set.

## 📱 Report output

### Hong Kong and U.S. market review

The report starts with market state, prevailing themes, index and sector performance, then lists upcoming macro events, earnings windows, and company events that still require confirmation. Hong Kong and U.S. reports use market-specific sources and rules.

### Opportunity scores and trade cards

Each stock retains the core conclusion, score, trend, risk alerts, and catalysts, with additional data-completeness, signal-confidence, and execution-state checks. A trade card is generated only when both the evidence and price structure meet the required conditions.

### U.S. social sentiment

When configured, the report includes Reddit, X, and Polymarket buzz, sentiment, mention counts, and prediction-market activity. Social sentiment provides event context; it never determines the trading conclusion by itself.

### Delivery brief structure

```text
HK/US Market Review and Opportunities | Date | Market State

Market State / Main Theme / Session Plan
Top Opportunities / Score / Data Completeness / Signal Confidence
Trigger / Invalidation / Stop / Target
Risk Alerts / Catalysts / Latest Developments
Upcoming Macro Events / Earnings Windows / Filing Checks
```

Supported channels include Telegram, email, WeCom, Feishu, DingTalk, Discord, Slack, Pushover, PushPlus, ServerChan 3, AstrBot, and custom webhooks.

## 🚀 Quick start

### Platforms

The Python application runs on Windows, Linux, and macOS. `run_hk_once.sh` and `run_us_once.sh` depend on Bash, `flock`, and GNU utilities and are intended mainly for Linux servers. Windows and macOS users can run the Python commands directly or use the operating system scheduler.

PDF output uses WeasyPrint; see the [official installation guide](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html) for platform-specific dependencies.

### Installation

Linux / macOS:

```bash
git clone https://github.com/Ir1svanquish/hk-us-market-research.git
cd hk-us-market-research
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Windows PowerShell:

```powershell
git clone https://github.com/Ir1svanquish/hk-us-market-research.git
Set-Location hk-us-market-research
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### Configuration

The committed `.env.example`, `.env.hk.example`, `.env.us.example`, and `.env.notifications.example` files are **credential-free templates**. They are intentionally kept in the repository to document the available fields.

Copy the templates before adding local settings:

```bash
cp .env.hk.example .env.hk
cp .env.us.example .env.us
```

Windows PowerShell:

```powershell
Copy-Item .env.hk.example .env.hk
Copy-Item .env.us.example .env.us
```

Filled `.env`, `.env.hk`, `.env.us`, and local key files are excluded by `.gitignore`. **Do not force-add them to Git.**

Settings added or used heavily by this repository:

| Variable | Purpose |
| --- | --- |
| `V2_HK_SYMBOLS` / `V2_US_SYMBOLS` | Hong Kong and U.S. structured-report universes |
| `V2_SEC_USER_AGENT` | SEC EDGAR request identity |
| `FINNHUB_API_KEYS` | U.S. earnings, expectations, and institutional data |
| `LONGBRIDGE_APP_KEY` / `LONGBRIDGE_APP_SECRET` / `LONGBRIDGE_ACCESS_TOKEN` | Hong Kong and U.S. realtime and valuation fields |
| `SOCIAL_SENTIMENT_API_KEY_FILE` | Local key file for U.S. Reddit, X, and Polymarket sentiment data |
| `LLM_CHANNELS` / `LITELLM_MODEL` | Model channels and model name |
| `TAVILY_API_KEYS` / `SERPAPI_API_KEYS` | News search; other upstream-supported providers can also be configured |

### Running

Base analysis:

```bash
cp .env.hk .env
python main.py --no-notify
```

Complete Linux server workflows:

```bash
./run_hk_once.sh
./run_us_once.sh
```

The complete workflow builds the base analysis and structured report, then uses local state to prevent duplicate processing and delivery.

On Windows or macOS, run the structured report module after the base report has been generated:

```bash
python -m v2.shadow_delivery --market hk --date YYYY-MM-DD --env-file .env.hk --delivery-mode v2
```

For the U.S. workflow, replace `hk` and `.env.hk` with `us` and `.env.us`.

## 📦 Current release scope

This repository publishes the command-line analysis, scheduled-report, and multi-channel delivery path:

- Connected to the main pipeline: decision dashboards, HK/U.S. market reviews, official-source checks, opportunity scoring, trade cards, social sentiment, and report delivery
- Core code retained: the Agent framework, 11 built-in strategies, image ticker extraction, CSV/Excel parsing, name completion, and portfolio import
- No complete entry point yet: long-running Telegram/Discord Bot processes and the Web/API import interface
- Not included: the upstream Web/desktop workspace, FastAPI service, Docker deployment, and GitHub Actions workflows
- The documented and verified scope is Hong Kong and U.S. stocks; inherited A-share and ETF code is outside the primary release scope

## Tests

```bash
python -m pytest tests -q
python -m pytest v2/tests -q
```

## Upstream project and license

This repository retains the upstream MIT License and copyright notice. For the general feature set, full Web/API deployment, and support for other markets, see:

- [ZhuLinsen/daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis)
- [Upstream README](https://github.com/ZhuLinsen/daily_stock_analysis/blob/main/README.md)

## Disclaimer

This project is intended for research and information organization only. It is not investment advice. Third-party data may be delayed, rate-limited, or changed without notice; verify data definitions and licensing terms before production use.
