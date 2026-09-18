# HK & US Market Research Reports

[中文](README.md)

An automated research and reporting project for Hong Kong and U.S. stocks. It turns market data, company filings, macro releases, and news into daily market reviews, stock decision dashboards, and opportunity reports, with Markdown, HTML, PDF, and message delivery options.

## Project focus

This project builds on the analysis and notification components of [daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis). The upstream project covers more markets and includes complete Web, desktop, and API workspaces. This repository narrows the scope to Hong Kong and U.S. stocks, with more emphasis on report depth, official-source checks, and reliable delivery.

Key differences:

- A focused Hong Kong and U.S. workflow with less unrelated configuration and runtime overhead
- HKEX filings, board calendars, southbound flows, and HKD liquidity for Hong Kong research
- SEC filings, earnings windows, company guidance, and U.S. macro events for U.S. research
- Opportunity scores, data completeness, signal confidence, and standardized trade cards on top of the base decision dashboard
- Cross-session state and 1/5/20-day validation for historical Top 5 signals
- Separate mobile-friendly briefs and full research reports

## Data and research coverage

- Hong Kong market data from Longbridge, HKEX filings, and board calendars
- U.S. market data from Yahoo Finance, Finnhub, and SEC EDGAR
- Macro events from the Nasdaq U.S. economic calendar and BLS schedules, including CPI, PPI, labor, and central-bank events
- Market state, index performance, sector rotation, prevailing themes, and next-session watch points
- Technical structure, price and volume, fundamentals, news, risk alerts, and catalysts
- Event gates, opportunity scoring, trade cards, confidence levels, and cross-session state
- Markdown, HTML, and PDF output

## Reports and delivery

### Decision dashboard

Each stock receives a core conclusion, suggested action, score, and trend, together with key price levels, risk alerts, catalysts, and recent developments. The report places data and events in one decision framework instead of listing news without context.

### Hong Kong and U.S. market review

The market review summarizes the session, the main market theme, index and sector performance, and the macro or company events that still require attention. Hong Kong and U.S. reports use market-specific data sources and rules rather than a single generic template.

### Opportunity scores and trade cards

The structured report shows data completeness, signal confidence, and execution readiness. When the evidence is sufficient, a trade card includes trigger, invalidation, stop, and target levels. Conflicting or incomplete evidence results in a watch-only conclusion.

### Delivery brief

Each channel receives a format suited to its reading experience: messaging channels prioritize the brief and key risks, while email and attachments retain the full report. A typical report includes:

```text
HK/US Market Review and Opportunities | Date | Market State

Market State / Main Theme / Session Plan
Top Opportunities / Score / Data Completeness / Signal Confidence
Trigger / Invalidation / Stop / Target
Risk Alerts / Catalysts / Latest Developments
Upcoming Macro Events / Earnings Windows / Filing Checks
```

Supported delivery channels:

- Telegram and email
- WeCom, Feishu, and DingTalk robots
- Discord and Slack
- Pushover, PushPlus, and ServerChan 3
- AstrBot and custom webhooks

See `.env.notifications.example` for the available fields. Only configured channels are used.

## Platforms

- Python 3.10 or later
- The Python application runs on Windows, Linux, and macOS
- `run_hk_once.sh` and `run_us_once.sh` depend on Bash, `flock`, and GNU utilities, so they are intended primarily for Linux servers
- Windows users can run the Python commands directly or schedule them with Task Scheduler
- macOS users can run the Python application directly; the Bash automation scripts require compatible GNU utilities
- PDF output uses WeasyPrint; see the [official installation guide](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html) for platform-specific dependencies

## Installation

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

## Configuration

Hong Kong and U.S. runs use separate environment files.

Linux / macOS:

```bash
cp .env.hk.example .env.hk
cp .env.us.example .env.us
```

Windows PowerShell:

```powershell
Copy-Item .env.hk.example .env.hk
Copy-Item .env.us.example .env.us
```

Common settings:

| Variable | Purpose |
| --- | --- |
| `STOCK_LIST` | Comma-separated stock universe |
| `LONGBRIDGE_APP_KEY` / `LONGBRIDGE_APP_SECRET` / `LONGBRIDGE_ACCESS_TOKEN` | Longbridge market data |
| `FINNHUB_API_KEYS` | U.S. earnings and consensus data |
| `TUSHARE_TOKEN` | Optional Hong Kong fundamentals |
| `LLM_CHANNELS` / `LITELLM_MODEL` | Model channels and model name |
| `TAVILY_API_KEYS` / `SERPAPI_API_KEYS` | News search |
| `V2_HK_SYMBOLS` / `V2_US_SYMBOLS` | Structured-report stock universe |
| `V2_SEC_USER_AGENT` | SEC EDGAR request identity |

The full templates are `.env.example`, `.env.hk.example`, `.env.us.example`, and `.env.notifications.example`. They contain placeholders only. Keep real credentials in local environment files and never commit them.

## Running the reports

The base analysis runs directly on all three platforms.

Linux / macOS:

```bash
cp .env.hk .env
python main.py --no-notify
```

Windows PowerShell:

```powershell
Copy-Item .env.hk .env
python main.py --no-notify
```

Built-in scheduler:

```bash
python main.py --schedule
```

Linux servers can run the complete market workflows directly:

```bash
./run_hk_once.sh
./run_us_once.sh
```

These scripts build the base analysis and structured report, then use local markers to avoid processing the same market date twice. On Windows or macOS, the structured report can also be invoked directly after the base report has been generated:

```bash
python -m v2.shadow_delivery --market hk --date YYYY-MM-DD --env-file .env.hk --delivery-mode v2
```

For the U.S. workflow, replace `hk` and `.env.hk` with `us` and `.env.us`.

## Tests

```bash
python -m pytest tests -q
python -m pytest v2/tests -q
```

## Project layout

```text
.
├── main.py              # Base analysis entry point
├── data_provider/       # Market data and fundamentals
├── src/                 # Analysis, reporting, and delivery
├── strategies/          # Configurable research strategies
├── templates/           # Report templates
├── v2/                  # Events, scoring, and trade cards
├── tests/               # Base pipeline tests
├── run_hk_once.sh       # Hong Kong workflow (Linux)
└── run_us_once.sh       # U.S. workflow (Linux)
```

## Disclaimer

This project is intended for research and information organization only. It is not investment advice. Third-party data may be delayed, rate-limited, or changed without notice; verify data definitions and licensing terms before production use.

## License

[MIT](LICENSE)
