# Structured research pipeline

This package adds official-source checks and a structured decision layer to the base market report. It is used by the top-level `run_hk_once.sh` and `run_us_once.sh` workflows.

## What it does

- Collects company filings and market calendars from HKEX, SEC EDGAR, Nasdaq, BLS, and Finnhub
- Normalizes events into a shared data contract
- Builds opportunity scores, confidence levels, and trade cards
- Tracks decision quality across market sessions
- Produces integrated HTML and PDF reports
- Keeps delivery idempotent so the same report is not sent twice

## Main modules

```text
v2/
├── official_sources/       # Filings, calendars, event context, and trade cards
├── data_contract.py        # Shared report schema
├── opportunity_scoring.py  # Ranking and confidence logic
├── integrated_report.py    # Final report assembly
├── shadow_delivery.py      # Market workflow and delivery state
├── top5_validation.py      # Historical validation
└── tests/                  # Offline regression tests
```

## Configuration

Copy the market-specific template at the repository root and fill in only the services you use:

```bash
cp .env.hk.example .env.hk
cp .env.us.example .env.us
```

The main structured-report settings are:

```dotenv
V2_HK_SYMBOLS=
V2_US_SYMBOLS=
V2_SEC_USER_AGENT=hk-us-market-research your-contact@example.com
FINNHUB_API_KEYS=
```

SEC EDGAR asks clients to provide a descriptive user agent with a contact address. Keep credentials in local environment files; generated output and state are ignored by Git.

## Run directly

The top-level scripts are the normal entry point. For a direct run:

```bash
python -m v2.shadow_delivery \
  --market hk \
  --date YYYY-MM-DD \
  --env-file .env.hk \
  --delivery-mode v2
```

Use `--market us` with `.env.us` for the U.S. workflow.

## Tests

```bash
python -m pytest v2/tests -q
```
