from __future__ import annotations

from pathlib import Path
import base64
import re
from urllib.parse import quote

import markdown
from weasyprint import HTML

CSS = '''
@page { size: A4; margin: 14mm 13mm 15mm; }
body {
  font-family: "Noto Sans CJK SC", "Noto Sans CJK", "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
  color: #111827;
  line-height: 1.6;
  font-size: 12px;
  letter-spacing: 0 !important;
  word-spacing: 0 !important;
  font-kerning: normal;
  font-variant-ligatures: none;
  overflow-wrap: anywhere;
  word-break: break-word;
}
.report {
  max-width: 100%;
}
p, li, blockquote {
  letter-spacing: 0 !important;
  word-spacing: 0 !important;
  font-kerning: normal;
  overflow-wrap: anywhere;
  word-break: break-word;
}
p {
  margin: 0.28em 0 0.52em;
}
ul, ol {
  margin: 0.25em 0 0.7em 1.2em;
  padding: 0;
}
li {
  margin: 0.15em 0;
}
h1, h2, h3, h4 {
  font-family: "Noto Sans CJK SC", "Noto Sans CJK", "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
  color: #0f172a;
  letter-spacing: 0 !important;
  word-spacing: 0 !important;
  break-after: avoid-page;
  line-height: 1.35;
  font-stretch: normal;
  font-synthesis: none;
  font-feature-settings: "kern" 1;
  font-variant-east-asian: normal;
}
h1 {
  margin: 1.1em 0 0.45em;
  padding: 0 0 6px;
  font-size: 19px;
  font-weight: 700;
  border-bottom: 2.2px solid #0f172a;
}
h1:first-child {
  margin-top: 0;
}
h2 {
  margin: 0.95em 0 0.4em;
  padding: 4px 0 2px 10px;
  font-size: 15px;
  font-weight: 700;
  border-left: 4px solid #2563eb;
  background: linear-gradient(90deg, rgba(37,99,235,0.10), rgba(37,99,235,0.02) 72%, transparent);
}
h3 {
  margin: 0.8em 0 0.28em;
  font-size: 13px;
  font-weight: 700;
}
h4 {
  margin: 0.7em 0 0.2em;
  font-size: 12px;
  font-weight: 700;
}
code { background: #f3f4f6; padding: 0.1em 0.3em; border-radius: 4px; }
pre { background: #f8fafc; padding: 10px; border: 1px solid #e5e7eb; overflow: auto; }
table {
  width: 100%;
  margin: 0.55em 0 0.9em;
  border-collapse: collapse;
  font-size: 10px;
  table-layout: auto;
  border: 1px solid #cbd5e1;
}
th, td {
  border: 1px solid #d1d5db;
  padding: 6px 7px;
  vertical-align: top;
  letter-spacing: 0 !important;
  word-spacing: 0 !important;
  font-kerning: normal;
  white-space: normal;
  word-break: break-word;
  overflow-wrap: anywhere;
  max-width: 0;
}
th {
  background: #e8eefc;
  color: #0f172a;
  font-weight: 800;
}
tbody tr:nth-child(even) td {
  background: #f8fafc;
}
blockquote {
  border-left: 4px solid #93c5fd;
  background: #eff6ff;
  margin: 0.35em 0 0.85em;
  padding: 8px 12px;
  color: #334155;
}
hr { border: none; border-top: 1px solid #d1d5db; margin: 18px 0; }
tr { page-break-inside: avoid; break-inside: avoid; }
td p, th p { margin: 0; }
h1, h2, h3, h4, p, li, td, th, blockquote, span, div { max-width: 100%; box-sizing: border-box; }
.field-label {
  font-weight: 700;
  color: #0f172a;
}
.lead-label {
  font-weight: 700;
  color: #0f172a;
}
.lead-row {
  margin: 0.22em 0 0.44em;
}
.list-intro {
  margin: 0.55em 0 0.18em;
  font-weight: 700;
  color: #0f172a;
}
.emoji-icon { width: 1.1em; height: 1.1em; vertical-align: -0.14em; margin: 0 0.08em; }
.emoji-icon--label { width: 1.34em; height: 1.34em; vertical-align: -0.21em; margin-right: 0.15em; }
.emoji-icon--lg { width: 1.28em; height: 1.28em; vertical-align: -0.18em; margin-right: 0.14em; }
'''


def _font_face_css(font_family: str, raw_path: str, *, weight: int = 400) -> str:
    path = Path(raw_path)
    if not path.exists():
        return ''
    encoded = base64.b64encode(path.read_bytes()).decode('ascii')
    mime = 'font/ttf'
    if path.suffix.lower() == '.otf':
        mime = 'font/otf'
    elif path.suffix.lower() == '.ttc':
        mime = 'font/collection'
    return (
        '@font-face {'
        f'font-family: "{font_family}";'
        f'src: url(data:{mime};base64,{encoded});'
        f'font-weight: {weight};'
        'font-style: normal;'
        '}'
    )


def _resolve_font_face() -> str:
    return ''


def _normalize_for_pdf(text: str) -> str:
    emoji_map = {
        '⚠️': '⚠',
    }
    for old, new in emoji_map.items():
        text = text.replace(old, new)
    return text


def _svg_data_uri(svg: str) -> str:
    return f"data:image/svg+xml;utf8,{quote(svg)}"


def _build_emoji_icon_html(emoji: str, *, large: bool = False) -> str:
    class_name = 'emoji-icon'
    if emoji in {'🆕', '💼'}:
        class_name += ' emoji-icon--label'
    elif large:
        class_name += ' emoji-icon--lg'
    if emoji == '🎯':
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<circle cx="32" cy="32" r="28" fill="#ef4444"/>'
            '<circle cx="32" cy="32" r="21" fill="#ffffff"/>'
            '<circle cx="32" cy="32" r="15" fill="#ef4444"/>'
            '<circle cx="32" cy="32" r="8" fill="#ffffff"/>'
            '<circle cx="32" cy="32" r="4" fill="#2563eb"/>'
            '<path d="M42 12l10 2-5 5 5 5-6 1-2 10-4-4-6 7-3-3 7-6-4-4z" fill="#2563eb"/>'
            '</svg>'
        )
    elif emoji == '📊':
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<rect x="6" y="6" width="52" height="52" rx="8" fill="#ffffff" stroke="#cbd5e1" stroke-width="3"/>'
            '<rect x="14" y="34" width="8" height="16" rx="2" fill="#60a5fa"/>'
            '<rect x="28" y="22" width="8" height="28" rx="2" fill="#34d399"/>'
            '<rect x="42" y="14" width="8" height="36" rx="2" fill="#fbbf24"/>'
            '<path d="M12 50h40" stroke="#94a3b8" stroke-width="3" stroke-linecap="round"/>'
            '</svg>'
        )
    elif emoji == '📈':
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<rect x="6" y="6" width="52" height="52" rx="8" fill="#ffffff" stroke="#cbd5e1" stroke-width="3"/>'
            '<path d="M14 45l12-12 9 8 15-19" fill="none" stroke="#22c55e" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>'
            '<path d="M44 22h8v8" fill="none" stroke="#22c55e" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>'
            '</svg>'
        )
    elif emoji == '📉':
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<rect x="6" y="6" width="52" height="52" rx="8" fill="#ffffff" stroke="#cbd5e1" stroke-width="3"/>'
            '<path d="M14 20l12 12 9-8 15 19" fill="none" stroke="#ef4444" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>'
            '<path d="M44 43h8v-8" fill="none" stroke="#ef4444" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>'
            '</svg>'
        )
    elif emoji in {'🟢', '🟡', '⚪', '🟠', '🔴'}:
        fill_map = {
            '🟢': '#7ac943',
            '🟡': '#facc15',
            '⚪': '#e5e7eb',
            '🟠': '#f59e0b',
            '🔴': '#ff4d4f',
        }
        stroke = '#d1d5db' if emoji == '⚪' else 'rgba(15, 23, 42, 0.14)'
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            f'<circle cx="32" cy="32" r="24" fill="{fill_map[emoji]}" stroke="{stroke}" stroke-width="3"/>'
            '</svg>'
        )
    elif emoji in {'⚠', '⚠️'}:
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<path d="M32 10l24 42a4 4 0 0 1-3.46 6H11.46A4 4 0 0 1 8 52l24-42z" fill="#f59e0b"/>'
            '<rect x="29" y="23" width="6" height="18" rx="3" fill="#ffffff"/>'
            '<circle cx="32" cy="47" r="3.2" fill="#ffffff"/>'
            '</svg>'
        )
    elif emoji == '✅':
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<rect x="8" y="8" width="48" height="48" rx="12" fill="#22c55e"/>'
            '<path d="M20 32l8 8 16-18" fill="none" stroke="#ffffff" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>'
            '</svg>'
        )
    elif emoji == '❌':
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<circle cx="32" cy="32" r="24" fill="#ef4444"/>'
            '<path d="M23 23l18 18M41 23L23 41" fill="none" stroke="#ffffff" stroke-width="6" stroke-linecap="round"/>'
            '</svg>'
        )
    elif emoji == '🆕':
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<rect x="7" y="10" width="50" height="44" rx="9" fill="#38bdf8" stroke="#0f172a" stroke-opacity="0.14" stroke-width="2.5"/>'
            '<text x="32" y="38" text-anchor="middle" font-size="18" font-family="Arial, sans-serif" font-weight="700" fill="#ffffff">NEW</text>'
            '</svg>'
        )
    elif emoji == '💼':
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<rect x="12" y="22" width="40" height="26" rx="5" fill="#8b5a2b"/>'
            '<path d="M12 30h40" stroke="#6b4423" stroke-width="2.5"/>'
            '<rect x="24" y="15" width="16" height="8" rx="3" fill="#b07a45"/>'
            '<rect x="28" y="28" width="8" height="5" rx="1.5" fill="#f8fafc"/>'
            '<path d="M24 22v-3a3 3 0 0 1 3-3h10a3 3 0 0 1 3 3v3" fill="none" stroke="#6b4423" stroke-width="2.5"/>'
            '</svg>'
        )
    elif emoji == '📰':
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<rect x="10" y="10" width="44" height="44" rx="6" fill="#f8fafc" stroke="#94a3b8" stroke-width="2.5"/>'
            '<rect x="16" y="18" width="12" height="12" rx="2" fill="#93c5fd"/>'
            '<path d="M33 21h15M33 27h15M16 36h32M16 42h32" stroke="#64748b" stroke-width="3" stroke-linecap="round"/>'
            '</svg>'
        )
    elif emoji == '🚨':
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<rect x="12" y="46" width="40" height="6" rx="3" fill="#475569"/>'
            '<path d="M20 43V31c0-8 5-14 12-14s12 6 12 14v12z" fill="#ef4444"/>'
            '<path d="M24 24c2-3 5-5 8-5s6 2 8 5" fill="none" stroke="#fca5a5" stroke-width="3" stroke-linecap="round"/>'
            '<path d="M10 30l8 2M54 30l-8 2M16 18l5 6M48 18l-5 6" stroke="#f59e0b" stroke-width="3" stroke-linecap="round"/>'
            '</svg>'
        )
    elif emoji == '✨':
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<path d="M20 12l4 12 12 4-12 4-4 12-4-12-12-4 12-4z" fill="#facc15"/>'
            '<path d="M46 18l2.5 7.5L56 28l-7.5 2.5L46 38l-2.5-7.5L36 28l7.5-2.5z" fill="#fde68a"/>'
            '<circle cx="48" cy="46" r="4" fill="#fef3c7"/>'
            '</svg>'
        )
    elif emoji == '📌':
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<circle cx="32" cy="18" r="10" fill="#ef4444"/>'
            '<path d="M26 25h12l-3 14 6 6-3 3-6-6-6 12-3-1 7-14-5-5z" fill="#94a3b8"/>'
            '</svg>'
        )
    elif emoji == '📍':
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<path d="M32 55s16-16 16-28a16 16 0 1 0-32 0c0 12 16 28 16 28z" fill="#ef4444"/>'
            '<circle cx="32" cy="27" r="6.5" fill="#ffffff"/>'
            '</svg>'
        )
    elif emoji == '💡':
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<path d="M32 12a16 16 0 0 0-10 28c2.5 2 4 4.6 4 7h12c0-2.4 1.5-5 4-7A16 16 0 0 0 32 12z" fill="#facc15"/>'
            '<rect x="25" y="47" width="14" height="6" rx="2" fill="#94a3b8"/>'
            '<path d="M22 18l-4-4M42 18l4-4M16 30h-6M54 30h-6" stroke="#f59e0b" stroke-width="3" stroke-linecap="round"/>'
            '</svg>'
        )
    elif emoji == '💭':
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<ellipse cx="32" cy="28" rx="18" ry="13" fill="#bfdbfe"/>'
            '<circle cx="20" cy="45" r="5" fill="#bfdbfe"/>'
            '<circle cx="13" cy="52" r="3.5" fill="#bfdbfe"/>'
            '</svg>'
        )
    elif emoji == '📱':
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<rect x="20" y="8" width="24" height="48" rx="5" fill="#334155"/>'
            '<rect x="24" y="14" width="16" height="32" rx="2" fill="#bfdbfe"/>'
            '<circle cx="32" cy="50" r="2.5" fill="#e2e8f0"/>'
            '</svg>'
        )
    elif emoji == '📢':
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<path d="M14 32l24-14v28L14 32z" fill="#60a5fa"/>'
            '<rect x="38" y="24" width="7" height="16" rx="2" fill="#1d4ed8"/>'
            '<path d="M20 38l4 12h8l-5-12" fill="#64748b"/>'
            '<path d="M48 24c4 3 6 6 6 8s-2 5-6 8" fill="none" stroke="#f59e0b" stroke-width="3" stroke-linecap="round"/>'
            '</svg>'
        )
    elif emoji == '🔵':
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<circle cx="32" cy="32" r="24" fill="#3b82f6" stroke="rgba(15, 23, 42, 0.14)" stroke-width="3"/>'
            '</svg>'
        )
    elif emoji == '🛑':
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<path d="M24 8h16l16 16v16L40 56H24L8 40V24z" fill="#ef4444"/>'
            '<text x="32" y="38" text-anchor="middle" font-size="14" font-family="Arial, sans-serif" font-weight="700" fill="#ffffff">STOP</text>'
            '</svg>'
        )
    elif emoji == '🎊':
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<path d="M18 46l10-24 18 18-24 10z" fill="#8b5cf6"/>'
            '<path d="M24 40l18-18" stroke="#f8fafc" stroke-width="3" stroke-linecap="round"/>'
            '<circle cx="45" cy="18" r="4" fill="#facc15"/>'
            '<circle cx="52" cy="28" r="3" fill="#22c55e"/>'
            '<circle cx="38" cy="12" r="2.5" fill="#ef4444"/>'
            '</svg>'
        )
    elif emoji == '💰':
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<path d="M24 18c0-4 16-4 16 0v5c6 3 10 8 10 16 0 11-8 18-18 18s-18-7-18-18c0-8 4-13 10-16z" fill="#22c55e"/>'
            '<path d="M24 22h16" stroke="#166534" stroke-width="3" stroke-linecap="round"/>'
            '<text x="32" y="43" text-anchor="middle" font-size="20" font-family="Arial, sans-serif" font-weight="700" fill="#fef3c7">$</text>'
            '</svg>'
        )
    else:
        return emoji

    return f'<img alt="{emoji}" class="{class_name}" src="{_svg_data_uri(svg)}" />'


def _replace_emojis_with_icons(html: str) -> str:
    large_icons = {'🎯', '📊', '📈', '📉'}
    emoji_order = [
        '⚠', '✅', '❌',
        '🎯', '📊', '📈', '📉',
        '🆕', '💼', '📰', '🚨', '✨', '📌', '📍', '💡', '💭', '📱', '📢',
        '🟢', '🟡', '⚪', '🟠', '🔴', '🔵', '🛑', '🎊', '💰',
    ]
    for emoji in emoji_order:
        html = html.replace(emoji, _build_emoji_icon_html(emoji, large=emoji in large_icons))
    return html


def _tighten_numeric_table_cells(html: str) -> str:
    return re.sub(r'<td>((?:[0-9][0-9.,%+-]*)|(?:[🟢🔴🟡⚪]\s*[+-]?[0-9][0-9.,%+-]*))</td>', r'<td class="num-col"><span class="numeric-cell">\1</span></td>', html)


def _tighten_plain_text_numbers(html: str) -> str:
    def repl(match):
        text = match.group(1)
        text = re.sub(r'([+-]?\d[\d,.]*%?)', r'<span class="inline-number">\1</span>', text)
        return f'>{text}<'
    return re.sub(r'>([^<>]+)<', repl, html)


def _inject_table_classes(html: str) -> str:
    html = html.replace('<table>', '<table class="oc-table">')
    html = html.replace('<table class="oc-table">\n<thead>\n<tr>\n<th>持仓情况</th>\n<th>操作建议</th>', '<table class="oc-table advice-table">\n<thead>\n<tr>\n<th>持仓情况</th>\n<th>操作建议</th>')
    html = html.replace('<table class="oc-table">\n<thead>\n<tr>\n<th>股票</th>\n<th>动作</th>\n<th>理由</th>', '<table class="oc-table summary-table">\n<thead>\n<tr>\n<th>股票</th>\n<th>动作</th>\n<th>理由</th>')
    return html


def _enhance_semantic_blocks(html: str) -> str:
    html = re.sub(r'<h1>(.*?)</h1>', r'<h1 class="section-title">\1</h1>', html)
    html = re.sub(r'<h2>(.*?)</h2>', r'<h2 class="stock-title">\1</h2>', html)
    html = re.sub(r'<h3>(.*?)</h3>', r'<h3 class="subsection-title">\1</h3>', html)

    intro_labels = (
        '市场状态', '当前主线', '今日策略', '逻辑', '观察重点', '失效条件',
    )
    for label in intro_labels:
        pattern = rf'<p>{label}：\s*(.*?)</p>'
        repl = rf'<p class="lead-row"><span class="lead-label">{label}：</span>\1</p>' if label in {'市场状态', '当前主线', '今日策略'} else rf'<p><span class="field-label">{label}：</span>\1</p>'
        html = re.sub(pattern, repl, html, flags=re.S)

    list_titles = ('今日最值得关注：', '今日不建议重仓参与：', '强势：', '转弱：', '观察：')
    for title in list_titles:
        html = html.replace(f'<p>{title}</p>', f'<p class="list-intro">{title}</p>')

    return html


def markdown_to_pdf(markdown_text: str, output_path: str) -> str:
    markdown_text = _normalize_for_pdf(markdown_text)
    html_body = markdown.markdown(markdown_text, extensions=['tables', 'fenced_code', 'nl2br'])
    html_body = _inject_table_classes(html_body)
    html_body = _enhance_semantic_blocks(html_body)
    html_body = _tighten_numeric_table_cells(html_body)
    html_body = _tighten_plain_text_numbers(html_body)
    html_body = _replace_emojis_with_icons(html_body)
    font_face = _resolve_font_face()
    css = CSS + '\n.numeric-cell { font-family: "Helvetica", "Arial", "Liberation Sans", "DejaVu Sans", sans-serif !important; letter-spacing: -0.02em !important; word-spacing: 0 !important; font-kerning: normal; white-space: nowrap; }\n.inline-number { font-family: "Helvetica", "Arial", "Liberation Sans", "DejaVu Sans", sans-serif !important; letter-spacing: -0.02em !important; word-spacing: 0 !important; font-kerning: normal; white-space: nowrap; }\n.oc-table { width: 100%; }\n.oc-table td.num-col { white-space: nowrap; }\n.advice-table th:nth-child(1), .advice-table td:nth-child(1) { width: 18%; white-space: nowrap; }\n.advice-table th:nth-child(2), .advice-table td:nth-child(2) { width: 82%; }\n.summary-table th:nth-child(1), .summary-table td:nth-child(1) { width: 14%; white-space: nowrap; }\n.summary-table th:nth-child(2), .summary-table td:nth-child(2) { width: 10%; white-space: nowrap; }\n.summary-table th:nth-child(3), .summary-table td:nth-child(3) { width: 76%; }\n' 
    html = f'<html><head><meta charset="utf-8"><style>{font_face}{css}</style></head><body><div class="report">{html_body}</div></body></html>'
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html).write_pdf(str(out))
    return str(out)
