# HK & US Market Research Reports

[中文](README.md)

A Python project for collecting Hong Kong and U.S. market information and turning it into daily research reports. It combines quotes, company filings, macro data, and news, then produces Markdown, HTML, or PDF reports. Reports can also be delivered through commonly used notification channels.

## What it covers

- Hong Kong market data from Longbridge, HKEX filings, and board calendars
- U.S. market data from Yahoo Finance, Finnhub, and SEC EDGAR
- Macro events from the Nasdaq U.S. economic calendar and BLS schedules
- Technical structure, price and volume, fundamentals, sector rotation, news, and event risk
- Structured event context, opportunity scoring, trade cards, confidence levels, and cross-session state
- Markdown, HTML, and PDF output
- Optional delivery through Telegram, email, WeCom, Feishu, Discord, Slack, and other channels

## Requirements

- Python 3.10 or later
- Linux or macOS
- PDF output requires the system packages used by WeasyPrint; see the [official installation guide](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#linux)

## Installation

```bash
git clone https://github.com/Ir1svanquish/hk-us-market-research.git
cd hk-us-market-research

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Hong Kong and U.S. runs use separate environment files:

```bash
cp .env.hk.example .env.hk
cp .env.us.example .env.us
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

The full templates are available in:

- `.env.example`
- `.env.hk.example`
- `.env.us.example`
- `.env.notifications.example`

These files contain placeholders only. Keep real credentials in local environment files and never commit them.

## Running the reports

Build the base report without sending notifications:

```bash
cp .env.hk .env
python main.py --no-notify
```

Run the complete workflow for each market:

```bash
./run_hk_once.sh
./run_us_once.sh
```

The scripts build the base analysis first and then create the structured report. Local run markers prevent the base pipeline from being processed twice on the same market date.

## Report delivery

Reports are sent to every channel that has been configured. Supported channels include:

- Telegram and email
- WeCom, Feishu, and DingTalk robots
- Discord and Slack
- Pushover, PushPlus, and ServerChan 3
- AstrBot and custom webhooks

See `.env.notifications.example` for the available fields, then copy only the settings you use into `.env.hk` or `.env.us`.

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
├── run_hk_once.sh       # Hong Kong workflow
└── run_us_once.sh       # U.S. workflow
```

## Disclaimer

This project is intended for research and information organization only. It is not investment advice. Third-party data may be delayed, rate-limited, or changed without notice; verify data definitions and licensing terms before production use.

## License

[MIT](LICENSE)
