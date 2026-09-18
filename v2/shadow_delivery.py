"""Generate and deliver paired production/shadow PDFs for one market session."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
V2_ROOT = Path(__file__).resolve().parent


def _load_env(path: Path) -> None:
    from dotenv import load_dotenv

    load_dotenv(path, override=True)


def _fmt(value: Any, suffix: str = "%", signed: bool = False) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    sign = "+" if signed and number > 0 else ""
    return f"{sign}{number:.1f}{suffix}"


def _top5_contract_rows(contract: Mapping[str, Any], market: str) -> list[Mapping[str, Any]]:
    rows = [
        item
        for item in contract.get("stocks") or []
        if isinstance(item, Mapping)
        and item.get("identity", {}).get("market") == market
        and int(item.get("opportunity", {}).get("rank") or 999) <= 5
    ]
    return sorted(rows, key=lambda item: int(item.get("opportunity", {}).get("rank") or 999))


def build_comparison_text(
    summary: Mapping[str, Any],
    contract: Mapping[str, Any],
    market: str,
    report_date: str,
) -> str:
    label = "港股" if market == "hk" else "美股"
    node = (summary.get("markets") or {}).get(market) or {}
    rolling = node.get("rolling_20") or {}
    shadow = node.get("shadow_rolling_20") or {}
    comparison = node.get("comparison") or {}
    verdict = node.get("promotion_verdict") or {}
    run = node.get("latest_shadow_run") or {}
    new_rows = _top5_contract_rows(contract, market)
    name_map = {
        str(item.get("identity", {}).get("symbol") or ""): str(item.get("identity", {}).get("name") or "")
        for item in contract.get("stocks") or []
        if isinstance(item, Mapping)
    }
    old_symbols = json.loads(run.get("production_top5_json") or "[]")
    new_symbols = [str(item.get("identity", {}).get("symbol") or "") for item in new_rows]
    old_text = "、".join(name_map.get(symbol) or symbol for symbol in old_symbols) or "未记录"
    new_text = "、".join(
        f"{item.get('identity', {}).get('name') or item.get('identity', {}).get('symbol')}"
        f"({item.get('opportunity', {}).get('score')}·{item.get('execution', {}).get('status')})"
        for item in new_rows
    ) or "未生成"
    added = [name_map.get(symbol) or symbol for symbol in new_symbols if symbol not in old_symbols]
    removed = [name_map.get(symbol) or symbol for symbol in old_symbols if symbol not in new_symbols]
    paired = int(comparison.get("paired_periods_5d") or 0)
    progress = min(100, paired / 40 * 100)
    lines = [
        f"📊 {label}新旧报告影子对比｜{report_date}",
        f"当前结论：{verdict.get('label') or '证据不足'}（5日配对 {paired}/40期，进度 {progress:.0f}%）",
        f"同日Top5重合：{_fmt(run.get('overlap_ratio'))}",
        f"旧版：{old_text}",
        f"新版：{new_text}",
        f"新版新增：{'、'.join(added) or '无'}；移出：{'、'.join(removed) or '无'}",
        "",
        f"旧版滚动{rolling.get('periods', 0)}期：1日 {_fmt(rolling.get('average_return_1d'), signed=True)} / 胜率 {_fmt(rolling.get('win_rate_1d'))}；5日 {_fmt(rolling.get('average_return_5d'), signed=True)} / 胜率 {_fmt(rolling.get('win_rate_5d'))}",
        f"新版影子{shadow.get('periods', 0)}期：1日 {_fmt(shadow.get('average_return_1d'), signed=True)} / 胜率 {_fmt(shadow.get('win_rate_1d'))}；触发 {shadow.get('triggered', 0)}/{shadow.get('trigger_eligible', 0)}，目标/止损 {shadow.get('target_1_hits', 0)}/{shadow.get('stop_hits', 0)}",
        f"同期1日平均：旧 {_fmt(comparison.get('production_average_1d'), signed=True)} / 新 {_fmt(comparison.get('shadow_average_1d'), signed=True)}",
        f"质量：官方/相对强弱/量能覆盖 {_fmt(run.get('official_coverage'))}/{_fmt(run.get('relative_strength_coverage'))}/{_fmt(run.get('volume_coverage'))}",
        "权重：样本达标前维持35/25/20/15/5。初步判断约5周，较可靠结论约8–12周。",
    ]
    return "\n".join(lines)[:1490]


def _compact_sentence(value: Any, limit: int = 92) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return "详见附件"
    if len(text) <= limit:
        return text
    clipped = text[:limit]
    for marker in ("。", "；", "，", ",", ";"):
        pos = clipped.rfind(marker)
        if pos >= limit // 2:
            clipped = clipped[:pos]
            break
    return clipped.rstrip("，,；;。") + "。"


def _reader_theme(value: Any) -> str:
    """Drop internal extraction placeholders from reader-facing summaries."""
    theme = re.sub(r"\s+", "", str(value or ""))
    if not theme or theme in {"未识别明确板块标签", "未识别板块标签", "未识别"}:
        return ""
    return str(value).strip()


def _named_primary_risk(row: Mapping[str, Any] | None, limit: int = 110) -> str:
    if not row:
        return "详见附件"
    identity = row.get("identity") or {}
    opportunity = row.get("opportunity") or {}
    name = str(identity.get("name") or identity.get("symbol") or "相关公司").strip()
    symbol = str(identity.get("symbol") or "").strip()
    company = f"{name}（{symbol}）" if symbol and symbol not in name else name
    risk = str(opportunity.get("primary_risk") or "").strip()
    return _compact_sentence(f"{company}：{risk}", limit)


def _recent_top5_lines(
    market: str,
    report_date: str,
    ledger_path: Path,
    name_map: Mapping[str, str],
) -> list[str]:
    if not ledger_path.exists():
        return []
    try:
        with sqlite3.connect(ledger_path) as connection:
            rows = connection.execute(
                """
                SELECT as_of, top5_json
                FROM shadow_run_audits
                WHERE market = ? AND as_of <= ?
                ORDER BY as_of DESC
                LIMIT 10
                """,
                (market, report_date),
            ).fetchall()
    except sqlite3.Error:
        return []
    entries: list[list[str]] = []
    for _, raw in reversed(rows):
        try:
            symbols = [str(item) for item in json.loads(raw or "[]") if item]
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if symbols:
            entries.append(symbols[:5])
    if not entries:
        return []
    frequency = Counter(symbol for symbols in entries for symbol in symbols)
    highlighted = sorted(frequency.items(), key=lambda item: (-item[1], item[0]))[:3]
    frequency_text = " / ".join(
        f"{name_map.get(symbol) or symbol}（{count}次）" for symbol, count in highlighted
    )
    streak_parts = []
    for symbol in entries[-1]:
        streak = 0
        for symbols in reversed(entries):
            if symbol not in symbols:
                break
            streak += 1
        if streak >= 2:
            streak_parts.append(f"{name_map.get(symbol) or symbol}（{streak}期）")
        if len(streak_parts) >= 3:
            break
    return [
        f"近{len(entries)}期高频：{frequency_text or '暂无'}",
        f"连续上榜：{' / '.join(streak_parts) or '暂无'}",
    ]


def build_v2_digest(
    contract: Mapping[str, Any],
    validation: Mapping[str, Any],
    market: str,
    report_date: str,
    *,
    hk_variables: Mapping[str, Any] | None = None,
    ledger_path: Path | None = None,
) -> tuple[str, str]:
    """Build the reader-facing V2 email/Telegram digest."""
    label = "港股" if market == "hk" else "美股"
    title = f"{label}复盘及机会日报"
    top_rows = _top5_contract_rows(contract, market)
    scores = [float(item.get("opportunity", {}).get("score") or 0) for item in top_rows]
    average_score = sum(scores) / len(scores) if scores else 0
    if market == "hk" and hk_variables:
        market_state = str((hk_variables.get("signal") or {}).get("label") or "中性")
    elif average_score >= 70:
        market_state = "偏强"
    elif average_score >= 60:
        market_state = "分化整理"
    else:
        market_state = "中性偏弱"
    subject = f"{title}｜{report_date}｜{market_state}"

    themes: list[str] = []
    theme_keys: set[str] = set()
    for item in top_rows:
        theme = _reader_theme(item.get("opportunity", {}).get("themes"))
        theme_key = re.sub(r"[\s/／]+", "", theme)
        if theme and theme_key not in theme_keys:
            themes.append(theme)
            theme_keys.add(theme_key)
    theme_text = "、".join(themes[:3]) or "等待市场主线进一步确认"

    if market == "hk" and hk_variables:
        key_variables = _compact_sentence((hk_variables.get("signal") or {}).get("summary"), 130)
        southbound = hk_variables.get("southbound") or {}
        market_node = hk_variables.get("market") or {}
        risks = []
        if float(southbound.get("net_5d_hkd_bn") or 0) < 0:
            risks.append(f"南向5日累计净流出 {abs(float(southbound.get('net_5d_hkd_bn'))):.2f} 十亿港元")
        if float(market_node.get("distance_to_weak_side") or 9) < 0.02:
            risks.append("USD/HKD 接近弱方兑换保证区间")
        market_risk = "；".join(risks) or _named_primary_risk(top_rows[0] if top_rows else None)
    else:
        key_variables = "重点宏观数据、未来7日高影响事件与AI拥挤度详见附件。"
        market_risk = _named_primary_risk(top_rows[0] if top_rows else None)

    lines = [
        f"# {title}",
        "",
        "## 今日市场结论",
        "",
        f"- **状态：** {market_state}",
        f"- **主线：** {theme_text}",
        f"- **关键变量：** {key_variables}",
        f"- **主要风险：** {market_risk}",
        "",
        "## 今日机会 Top5",
    ]
    for row in top_rows:
        identity = row.get("identity") or {}
        opportunity = row.get("opportunity") or {}
        execution = row.get("execution") or {}
        card = execution.get("standard_trade_card") or {}
        lines.extend(
            [
                "",
                f"### {opportunity.get('rank')}｜{identity.get('name') or identity.get('symbol')}｜机会分 {opportunity.get('score')}｜{execution.get('status') or '研究关注'}",
                "",
                f"- **看点：** {_compact_sentence(opportunity.get('primary_catalyst'))}",
                f"- **确认：** {_compact_sentence(card.get('watch_condition') or card.get('trigger_condition'))}",
                f"- **失效：** {_compact_sentence(card.get('invalidation_condition'))}",
            ]
        )

    earnings: list[tuple[int, str]] = []
    for row in contract.get("stocks") or []:
        if not isinstance(row, Mapping) or row.get("identity", {}).get("market") != market:
            continue
        scenario = row.get("earnings_scenario") or {}
        days = scenario.get("trading_days_to_event")
        if scenario.get("status") == "upcoming" and isinstance(days, int) and 0 <= days <= 3:
            name = row.get("identity", {}).get("name") or row.get("identity", {}).get("symbol")
            timing = scenario.get("timing") or scenario.get("event_date") or "日期待确认"
            rank = int(row.get("opportunity", {}).get("rank") or 999)
            earnings.append((rank, f"{name}（{timing}）"))
    if earnings:
        earnings.sort(key=lambda item: (item[0], item[1]))
        displayed_earnings = [item[1] for item in earnings[:5]]
        earnings_text = "、".join(displayed_earnings)
        if len(earnings) > len(displayed_earnings):
            earnings_text += f"等 {len(earnings)} 只"
        lines.extend(
            [
                "",
                "## 未来 3 个交易日事件",
                "",
                f"- **财报：** {earnings_text}",
                "- **重点观察：** 公司指引、利润率、收入/订单增速及财报后的价格确认。",
            ]
        )

    market_validation = (validation.get("markets") or {}).get(market) or {}
    shadow = market_validation.get("shadow_rolling_20") or {}
    name_map = {
        str(item.get("identity", {}).get("symbol") or ""): str(item.get("identity", {}).get("name") or "")
        for item in contract.get("stocks") or []
        if isinstance(item, Mapping)
    }
    stats_lines = _recent_top5_lines(
        market,
        report_date,
        ledger_path or V2_ROOT / "state" / "decision_accuracy.sqlite3",
        name_map,
    )
    lines.extend(["", "## 近期验证", ""])
    lines.extend(f"- **{item.split('：', 1)[0]}：** {item.split('：', 1)[1]}" for item in stats_lines)
    sample_5d = int(shadow.get("sample_5d") or 0)
    lines.append(f"- **成熟 5 日样本：** {sample_5d} 条")
    if sample_5d:
        lines.append(
            f"- **5 日胜率 / 平均收益：** {_fmt(shadow.get('win_rate_5d'))} / {_fmt(shadow.get('average_return_5d'), signed=True)}"
        )
    else:
        lines.append("- **5 日验证：** 证据不足，暂不展示胜率结论")
    lines.extend(
        [
            "",
            "## 附件",
            "",
            f"- {label} V2 完整机会研究报告 PDF",
            "",
            "正文用于快速浏览；完整评分拆解、确认条件、失效位置、财报情景和历史验证请查看附件。",
        ]
    )
    return subject, "\n".join(lines)


def build_v2_telegram_digest(
    subject: str,
    contract: Mapping[str, Any],
    validation: Mapping[str, Any],
    market: str,
    *,
    hk_variables: Mapping[str, Any] | None = None,
) -> str:
    """Build a compact companion message that stays below Telegram's detail guard."""
    lines = [f"📡 {subject}", "", "今日机会 Top5："]
    for row in _top5_contract_rows(contract, market):
        identity = row.get("identity") or {}
        opportunity = row.get("opportunity") or {}
        execution = row.get("execution") or {}
        lines.append(
            f"{opportunity.get('rank')}. {identity.get('name') or identity.get('symbol')}｜"
            f"{opportunity.get('score')}分｜{execution.get('status') or '研究关注'}"
        )
        lines.append(f"   看点：{_compact_sentence(opportunity.get('primary_catalyst'), 54)}")
    if market == "hk" and hk_variables:
        summary = _compact_sentence((hk_variables.get("signal") or {}).get("summary"), 110)
        lines.extend(["", f"关键变量：{summary}"])
    shadow = ((validation.get("markets") or {}).get(market) or {}).get("shadow_rolling_20") or {}
    sample_5d = int(shadow.get("sample_5d") or 0)
    if sample_5d:
        lines.append(
            f"V2成熟5日样本：{sample_5d}条｜胜率 {_fmt(shadow.get('win_rate_5d'))}｜"
            f"平均 {_fmt(shadow.get('average_return_5d'), signed=True)}"
        )
    else:
        lines.append("V2成熟5日样本：0条｜当前证据不足")
    lines.extend(["", "完整确认、失效条件与财报情景请看附件 PDF。"])
    return "\n".join(lines)[:1490]


def _latest_earnings_research(report_date: str) -> Path | None:
    candidates = []
    for path in (V2_ROOT / "data").glob("earnings_research_*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(payload.get("as_of") or "")[:10] <= report_date:
            candidates.append((str(payload.get("as_of") or ""), path))
    return max(candidates)[1] if candidates else None


def _write_state(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def generate_shadow_report(
    *,
    market: str,
    report_date: str,
    env_file: Path,
    db_path: Path,
    output_dir: Path,
    reuse_built: bool = False,
) -> dict[str, Any]:
    from v2.full_pool_pipeline import main as build_full_pool
    from v2.integrated_report import render_report
    from v2.official_sources.cli import main as sync_official
    from v2.research_report import load_stocks

    token = report_date.replace("-", "")
    contract_path = output_dir / f"full_pool_contract_{token}_{market}.json"
    event_path = output_dir / f"full_pool_event_context_{token}_{market}.json"
    validation_path = output_dir / f"top5_validation_{token}_{market}.json"
    required_build = (contract_path, event_path, validation_path)
    symbols = [item.symbol for item in load_stocks(db_path, report_date, market, None)]
    if not symbols:
        raise RuntimeError(f"no {market} analysis rows for {report_date}")
    if not reuse_built or not all(path.exists() for path in required_build):
        sync_path = output_dir / f"full_pool_official_sync_{token}_{market}.json"
        sync_args = [
            "--lookback-days", "30", "--end-date", report_date,
            "--database", str(V2_ROOT / "state" / "official_filings.sqlite3"),
            "--output", str(sync_path), "--print-filings", "5",
        ]
        if market == "hk":
            sync_args.extend(["--hk-symbols", ",".join(symbols)])
        else:
            sync_args.extend(
                [
                    "--us-symbols", ",".join(symbols),
                    "--sec-contact-env-file", str(env_file),
                ]
            )
        if sync_official(sync_args) != 0:
            raise RuntimeError("official-source sync failed")

        pool_args = [
            "--date", report_date, "--market", market, "--db", str(db_path),
            "--official-sync", str(sync_path), "--output-dir", str(output_dir),
            "--env-hk", str(PROJECT_ROOT / ".env.hk"),
            "--env-us", str(PROJECT_ROOT / ".env.us"),
        ]
        earnings = _latest_earnings_research(report_date)
        if earnings:
            pool_args.extend(["--earnings-research", str(earnings)])
        if build_full_pool(pool_args) != 0:
            raise RuntimeError("full-pool shadow build failed")
    hk_variables_path = None
    if market == "hk":
        from v2.hk_key_variables import main as build_hk_variables

        hk_variables_path = output_dir / f"hk_key_variables_{token}_shadow.json"
        hk_variable_args = [
                "--as-of", report_date,
                "--output-html", str(output_dir / f"hk_key_variables_{token}_shadow.html"),
                "--output-pdf", str(output_dir / f"hk_key_variables_{token}_shadow.pdf"),
                "--output-json", str(hk_variables_path),
        ]
        prior_snapshots = sorted(
            path
            for path in output_dir.glob("hk_key_variables_*_shadow.json")
            if path != hk_variables_path and path.name < hk_variables_path.name
        )
        if prior_snapshots:
            hk_variable_args.extend(["--fallback-json", str(prior_snapshots[-1])])
        if (not reuse_built or not hk_variables_path.exists()) and build_hk_variables(hk_variable_args) != 0:
            raise RuntimeError("HK key-variable build failed")
    old_pdf = PROJECT_ROOT / "reports" / f"compact_report_{token}_{market}.pdf"
    if not old_pdf.exists():
        raise FileNotFoundError(f"production PDF missing: {old_pdf}")
    new_pdf = output_dir / f"shadow_report_{token}_{market}.pdf"
    new_html = output_dir / f"shadow_report_{token}_{market}.html"
    result = render_report(
        input_md=PROJECT_ROOT / "reports" / f"compact_report_{token}_{market}.md",
        market=market,
        db_path=db_path,
        contract_path=contract_path,
        event_context_path=event_path,
        output_html=new_html,
        output_pdf=new_pdf,
        hk_key_variables_path=hk_variables_path,
        validation_summary_path=validation_path,
        accuracy_ledger_path=V2_ROOT / "state" / "decision_accuracy.sqlite3",
    )
    if not result.get("layout_ok") or not result.get("content_ok"):
        raise RuntimeError(f"shadow report audit failed: {result}")
    summary = json.loads(validation_path.read_text(encoding="utf-8"))
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    return {
        "old_pdf": str(old_pdf),
        "new_pdf": str(new_pdf),
        "comparison": build_comparison_text(summary, contract, market, report_date),
        "report_result": result,
        "validation": str(validation_path),
        "contract": str(contract_path),
        "hk_variables": str(hk_variables_path) if hk_variables_path else None,
    }


def deliver(result: Mapping[str, Any], market: str, report_date: str, state_path: Path) -> dict[str, Any]:
    from src.notification import NotificationService

    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    notifier = NotificationService()
    label = "港股" if market == "hk" else "美股"
    if not state.get("comparison_sent"):
        state["comparison_sent"] = notifier.send_to_telegram(str(result["comparison"]))
        _write_state(state_path, state)
    if not state.get("old_pdf_sent"):
        state["old_pdf_sent"] = notifier.send_telegram_document(
            str(result["old_pdf"]), caption=f"{label}旧版正式报告｜{report_date}"
        )
        _write_state(state_path, state)
    if not state.get("new_pdf_sent"):
        state["new_pdf_sent"] = notifier.send_telegram_document(
            str(result["new_pdf"]), caption=f"{label}新版影子报告｜{report_date}｜不参与正式决策"
        )
        _write_state(state_path, state)
    state.update(
        {
            "market": market,
            "report_date": report_date,
            "completed": all(state.get(key) for key in ("comparison_sent", "old_pdf_sent", "new_pdf_sent")),
            "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
    )
    _write_state(state_path, state)
    return state


def deliver_v2(result: Mapping[str, Any], market: str, report_date: str, state_path: Path) -> dict[str, Any]:
    """Deliver V2 as the only reader-facing report; keep V1 artifacts private."""
    from src.notification import NotificationService

    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    contract = json.loads(Path(str(result["contract"])).read_text(encoding="utf-8"))
    validation = json.loads(Path(str(result["validation"])).read_text(encoding="utf-8"))
    hk_variables = None
    if result.get("hk_variables"):
        hk_variables = json.loads(Path(str(result["hk_variables"])).read_text(encoding="utf-8"))
    subject, digest = build_v2_digest(
        contract,
        validation,
        market,
        report_date,
        hk_variables=hk_variables,
        ledger_path=V2_ROOT / "state" / "decision_accuracy.sqlite3",
    )
    telegram_digest = build_v2_telegram_digest(
        subject,
        contract,
        validation,
        market,
        hk_variables=hk_variables,
    )
    notifier = NotificationService()
    telegram_available = getattr(notifier, "_is_telegram_configured", lambda: False)()
    email_available = getattr(notifier, "_is_email_configured", lambda: False)()

    if not telegram_available:
        state["telegram_summary_skipped"] = True
        state["telegram_pdf_skipped"] = True
    else:
        if not state.get("telegram_summary_sent"):
            state["telegram_summary_sent"] = notifier.send_to_telegram(telegram_digest)
            _write_state(state_path, state)
        if not state.get("telegram_pdf_sent"):
            state["telegram_pdf_sent"] = notifier.send_telegram_document(
                str(result["new_pdf"]), caption=subject
            )
            _write_state(state_path, state)

    if not email_available:
        state["email_skipped"] = True
    elif not state.get("email_sent"):
        label = "港股" if market == "hk" else "美股"
        state["email_sent"] = notifier.send_to_email(
            digest,
            subject=subject,
            receivers=notifier.get_all_email_receivers(),
            attachment_path=str(result["new_pdf"]),
            attachment_name=f"{label}复盘及机会日报_{report_date}.pdf",
        )
        _write_state(state_path, state)

    telegram_done = (
        state.get("telegram_summary_sent") and state.get("telegram_pdf_sent")
    ) or (state.get("telegram_summary_skipped") and state.get("telegram_pdf_skipped"))
    email_done = state.get("email_sent") or state.get("email_skipped")
    state.update(
        {
            "market": market,
            "report_date": report_date,
            "delivery_mode": "v2",
            "old_report_external_delivery": False,
            "comparison_generated": True,
            "completed": bool(telegram_done and email_done),
            "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
    )
    _write_state(state_path, state)
    return state


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate and send paired production/shadow reports")
    parser.add_argument("--market", choices=("hk", "us"), required=True)
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--db", type=Path, default=PROJECT_ROOT / "data" / "stock_analysis.db")
    parser.add_argument("--output-dir", type=Path, default=V2_ROOT / "output")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reuse-built", action="store_true")
    parser.add_argument("--force-delivery", action="store_true")
    parser.add_argument("--delivery-mode", choices=("paired", "v2"), default="paired")
    args = parser.parse_args(argv)
    env_file = args.env_file or PROJECT_ROOT / f".env.{args.market}"
    _load_env(env_file)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    token = args.date.replace("-", "")
    state_dir = "v2_delivery" if args.delivery_mode == "v2" else "shadow_delivery"
    state_path = PROJECT_ROOT / "state" / state_dir / f"{args.market}_{token}.json"
    if state_path.exists() and not args.force_delivery:
        existing = json.loads(state_path.read_text(encoding="utf-8"))
        if existing.get("completed"):
            print(json.dumps({"status": "already_delivered", "state": str(state_path)}, ensure_ascii=False))
            return 0
    result = generate_shadow_report(
        market=args.market,
        report_date=args.date,
        env_file=env_file,
        db_path=args.db,
        output_dir=args.output_dir,
        reuse_built=args.reuse_built,
    )
    if args.dry_run:
        print(json.dumps({"status": "dry_run", **result}, ensure_ascii=False, indent=2))
        return 0
    if args.delivery_mode == "v2":
        state = deliver_v2(result, args.market, args.date, state_path)
    else:
        state = deliver(result, args.market, args.date, state_path)
    print(json.dumps({"status": "delivered" if state.get("completed") else "partial", "state": state}, ensure_ascii=False, indent=2))
    return 0 if state.get("completed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
