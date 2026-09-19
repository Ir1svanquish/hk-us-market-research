"""Deterministic multi-label event classification for official filings."""

from __future__ import annotations

from typing import Dict, Iterable, List, Tuple


SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _contains(text: str, keywords: Iterable[str]) -> bool:
    normalized = text.casefold()
    return any(keyword.casefold() in normalized for keyword in keywords)


def _collect_tags(text: str, rules: Iterable[Tuple[str, str, Iterable[str]]]) -> List[Dict[str, str]]:
    tags: List[Dict[str, str]] = []
    seen = set()
    for event_type, severity, keywords in rules:
        if event_type not in seen and _contains(text, keywords):
            tags.append({"event_type": event_type, "severity": severity})
            seen.add(event_type)
    return tags


def _primary(tags: List[Dict[str, str]]) -> Tuple[str, str]:
    if not tags:
        return "other_official_filing", "medium"
    selected = max(enumerate(tags), key=lambda item: (SEVERITY_RANK[item[1]["severity"]], -item[0]))[1]
    return selected["event_type"], selected["severity"]


def classify_hkex_events(title: str, category: str, body_text: str = "") -> List[Dict[str, str]]:
    text = f"{title} {category} {body_text}"
    rules = (
        (
            "trading_status",
            "critical",
            (
                "suspension of trading",
                "continued suspension",
                "delisting",
                "winding up",
                "liquidation",
                "bankruptcy",
                "停牌",
                "除牌",
                "清盤",
                "清盘",
                "破產",
                "破产",
            ),
        ),
        (
            "earnings_warning",
            "high",
            ("profit warning", "profit alert", "盈利警告", "盈利預警", "盈利预警"),
        ),
        (
            "earnings_calendar",
            "high",
            ("date of board meeting", "notification of board meeting", "董事會會議日期", "董事会会议日期"),
        ),
        (
            "capital_dilution",
            "high",
            (
                "placing",
                "rights issue",
                "open offer",
                "convertible bond",
                "convertible note",
                "issue of shares",
                "issue of equity securities",
                "配售",
                "供股",
                "公開發售",
                "公开发售",
                "可換股",
                "可换股",
                "發行股份",
                "发行股份",
                "發行股票",
                "发行股票",
            ),
        ),
        (
            "debt_financing",
            "high",
            ("issue of debt securities", "bond issue", "發行債券", "发行债券"),
        ),
        (
            "financial_results",
            "high",
            (
                "annual results",
                "interim results",
                "quarterly results",
                "results announcement",
                "final results",
                "annual report",
                "interim report",
                "half-year report",
                "全年業績",
                "全年业绩",
                "中期業績",
                "中期业绩",
                "季度業績",
                "季度业绩",
                "業績公告",
                "业绩公告",
                "中期報告",
                "中期报告",
                "年度報告",
                "年度报告",
            ),
        ),
        (
            "inside_information",
            "high",
            ("inside information", "內幕消息", "内幕消息", "內幕資料", "内幕资料"),
        ),
        (
            "major_transaction",
            "high",
            (
                "very substantial",
                "major transaction",
                "connected transaction",
                "重大交易",
                "關連交易",
                "关联交易",
            ),
        ),
        (
            "audit_change",
            "high",
            ("change in auditors", "resignation of auditor", "核數師", "核数师", "審計師變更", "审计师变更"),
        ),
        (
            "strategic_investment",
            "medium",
            ("investment fund", "participation in investment", "產業投資基金", "产业投资基金", "參與投資", "参与投资"),
        ),
        (
            "dividend",
            "medium",
            ("dividend", "股息", "分紅", "分红"),
        ),
        (
            "share_buyback",
            "low",
            ("share buyback", "repurchase", "股份購回", "股份购回", "回購", "回购"),
        ),
        (
            "share_scheme",
            "medium",
            ("share scheme", "share incentive plan", "share award", "share option", "股份計劃", "股份计划", "股份獎勵", "股份奖励"),
        ),
        (
            "board_or_management",
            "medium",
            ("change of director", "chief executive", "company secretary", "董事變更", "董事变更", "行政總裁", "行政总裁"),
        ),
        (
            "routine_disclosure",
            "low",
            ("monthly return", "next day disclosure return", "月報表", "月报表", "翌日披露報表", "翌日披露报表"),
        ),
    )
    tags = _collect_tags(text, rules)
    # A generic monthly/next-day disclosure title only says that the issued
    # share count changed; it does not establish a placing or other dilutive
    # financing.  In particular, HKEX buyback returns often contain the phrase
    # "changes in issued shares", which used to match the shorter
    # "issue of shares" keyword and create a false 30-day dilution gate.
    routine_title = _contains(
        f"{title} {category}",
        ("monthly return", "next day disclosure return", "月報表", "月报表", "翌日披露報表", "翌日披露报表"),
    )
    explicit_financing_title = _contains(
        f"{title} {category}",
        (
            "placing",
            "rights issue",
            "open offer",
            "convertible bond",
            "convertible note",
            "配售",
            "供股",
            "公開發售",
            "公开发售",
            "可換股",
            "可换股",
        ),
    )
    if routine_title and not explicit_financing_title:
        tags = [tag for tag in tags if tag.get("event_type") != "capital_dilution"]
    general_mandate_only = _contains(
        f"{title} {category}",
        ("general mandate to issue shares", "general mandates to issue shares", "發行股份之一般授權", "发行股份之一般授权"),
    )
    if general_mandate_only and not explicit_financing_title:
        # A shareholder mandate is authorization, not evidence that a placing
        # or issuance has actually been launched.
        tags = [tag for tag in tags if tag.get("event_type") != "capital_dilution"]
    return tags or [{"event_type": "other_official_filing", "severity": "medium"}]


def classify_hkex_event(title: str, category: str, body_text: str = "") -> Tuple[str, str]:
    return _primary(classify_hkex_events(title, category, body_text))


def classify_sec_events(form_type: str, items: str = "", title: str = "") -> List[Dict[str, str]]:
    form = (form_type or "").upper().strip()
    item_set = {item.strip() for item in (items or "").split(",") if item.strip()}
    tags: List[Dict[str, str]] = []

    if form in {"10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A"}:
        tags.append({"event_type": "financial_results", "severity": "high"})
    elif form in {"4", "4/A"}:
        tags.append({"event_type": "insider_transaction", "severity": "low"})
    elif form in {"144", "144/A"}:
        tags.append({"event_type": "planned_insider_sale", "severity": "low"})
    elif form.startswith(("S-1", "S-3", "F-1", "F-3", "424B")) or form in {"POS AM", "EFFECT"}:
        tags.append({"event_type": "capital_dilution", "severity": "high"})
    elif form.startswith(("SC 13D", "SC 13G", "SCHEDULE 13D", "SCHEDULE 13G")):
        tags.append({"event_type": "beneficial_ownership", "severity": "medium"})
    elif form.startswith(("DEF 14A", "PRE 14A")):
        tags.append({"event_type": "proxy_governance", "severity": "medium"})
    elif form in {"6-K", "6-K/A"}:
        if _contains(f"{title} {items}", ("earnings", "results", "guidance", "profit warning")):
            tags.append({"event_type": "financial_results", "severity": "high"})
        else:
            tags.append({"event_type": "foreign_issuer_report", "severity": "medium"})
    elif form in {"8-K", "8-K/A"}:
        if item_set.intersection({"1.03", "3.01", "4.02"}):
            tags.append({"event_type": "critical_corporate_event", "severity": "critical"})
        if "1.05" in item_set:
            tags.append({"event_type": "cybersecurity_incident", "severity": "high"})
        if "2.02" in item_set:
            tags.append({"event_type": "financial_results", "severity": "high"})
        if "2.05" in item_set:
            tags.append({"event_type": "restructuring", "severity": "high"})
        if "4.01" in item_set:
            tags.append({"event_type": "audit_change", "severity": "high"})
        if "5.02" in item_set:
            tags.append({"event_type": "board_or_management", "severity": "high"})
        if not tags:
            tags.append({"event_type": "current_report", "severity": "medium"})
    return tags or [{"event_type": "other_official_filing", "severity": "medium"}]


def classify_sec_event(form_type: str, items: str = "", title: str = "") -> Tuple[str, str]:
    return _primary(classify_sec_events(form_type, items=items, title=title))
