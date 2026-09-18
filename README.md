<div align="center">

# 港美股研究报告系统

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> 自动整理港股和美股的行情、公告、宏观数据与新闻，生成每日复盘和个股研究报告，并按需推送到常用通知渠道。

[**功能特性**](#-功能特性) · [**快速开始**](#-快速开始) · [**推送效果**](#-推送效果) · [**配置说明**](#-配置说明)

简体中文 | [English](README_EN.md)

</div>

项目以 [daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis) 为基础，针对港股和美股的日常研究流程做了调整。

## ✨ 功能特性

| 能力 | 内容 |
| --- | --- |
| 每日研究报告 | 市场状态、主线方向、个股结论、评分、趋势、风险警报和催化因素 |
| 港股研究 | 长桥行情、HKEX 公告、董事会日历、南向资金和港元流动性 |
| 美股研究 | Yahoo Finance、Finnhub、SEC EDGAR、财报窗口和公司指引 |
| 宏观事件 | Nasdaq 美国经济日历、BLS 日程，以及 CPI、PPI、就业和央行事件 |
| 机会评分 | 数据完整度、信号置信度、机会强度和执行成熟度 |
| 交易卡 | 触发条件、失效条件、止损和目标位；证据不足时只保留观察结论 |
| 社交舆情 | Reddit、X、Polymarket 热度与情绪，仅用于美股，可选启用 |
| 历史验证 | 跟踪 Top 5 信号的 1/5/20 日表现、最大有利/不利波动和目标/止损命中 |
| 报告输出 | Markdown、HTML、PDF，以及适合手机阅读的简短摘要 |
| 自动化推送 | 本地定时运行，支持 Telegram、邮件、企业微信、飞书、钉钉、Discord、Slack 等渠道 |

### 数据与服务

| 类型 | 支持 |
| --- | --- |
| 模型 | OpenAI 兼容、DeepSeek、通义千问、Claude、Gemini、Ollama，以及 LiteLLM 多渠道路由 |
| 行情 | Longbridge、YFinance、Finnhub、AkShare、Tushare、Pytdx、Baostock、TickFlow |
| 官方来源 | HKEX、SEC EDGAR、Nasdaq、BLS |
| 新闻搜索 | Anspire、SerpAPI、Tavily、Bocha、Brave、MiniMax、SearXNG |
| 社交舆情 | Reddit、X、Polymarket（仅美股，可选） |
| 推送 | Telegram、邮件、企业微信、飞书、钉钉、Discord、Slack、Pushover、PushPlus、Server酱3、AstrBot、自定义 Webhook |

## 🚀 快速开始

### 1. 安装

```bash
git clone https://github.com/Ir1svanquish/hk-us-market-research.git
cd hk-us-market-research

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Windows PowerShell 激活虚拟环境：

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### 2. 配置

```bash
cp .env.hk.example .env.hk
cp .env.us.example .env.us
```

Windows PowerShell：

```powershell
Copy-Item .env.hk.example .env.hk
Copy-Item .env.us.example .env.us
```

仓库中的 `.env*.example` 都是空白模板，不含真实密钥。填写后的 `.env`、`.env.hk`、`.env.us` 和本地密钥文件不会被 Git 跟踪。

### 3. 运行

先测试基础分析，不发送通知：

```bash
cp .env.hk .env
python main.py --no-notify
```

Linux 服务器可直接运行完整流程：

```bash
./run_hk_once.sh
./run_us_once.sh
```

内置定时模式：

```bash
python main.py --schedule
```

Python 主程序支持 Windows、Linux 和 macOS。两个 `run_*_once.sh` 脚本依赖 Bash、`flock` 和 GNU 工具，主要用于 Linux；其他系统可以直接运行 Python 命令或使用系统自带的定时任务。

PDF 输出依赖 WeasyPrint，安装方式见 [官方文档](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html)。

## 📱 推送效果

### 每日摘要

```text
港股/美股复盘及机会日报｜日期｜市场状态

市场状态：震荡 / 偏强 / 风险收缩
当前主线：当日主要方向和核心矛盾
重点机会：评分、数据完整度、信号置信度
交易条件：触发、失效、止损、目标位
事件跟踪：财报窗口、宏观日程、官方公告
风险提示：个股风险、市场风险和数据缺口
```

### 个股研究

每只股票会给出核心结论、操作建议、评分和趋势，并整理技术结构、量价、基本面、最新消息、风险警报和催化因素。美股在启用社交舆情后，还会显示 Reddit、X 和 Polymarket 摘要。

### 完整报告

完整 PDF 包含市场复盘、机会排名、个股研究、事件核验和交易卡。即时通讯渠道优先发送简短摘要，邮件和附件保留完整内容。

## ⚙️ 配置说明

常用配置项：

| 配置 | 用途 |
| --- | --- |
| `STOCK_LIST` | 基础分析股票池，多个代码用逗号分隔 |
| `V2_HK_SYMBOLS` / `V2_US_SYMBOLS` | 港股 / 美股结构化报告股票池 |
| `LLM_CHANNELS` / `LITELLM_MODEL` | 模型渠道和模型名称 |
| `LONGBRIDGE_APP_KEY` / `LONGBRIDGE_APP_SECRET` / `LONGBRIDGE_ACCESS_TOKEN` | 港美股实时行情和估值字段 |
| `FINNHUB_API_KEYS` | 美股财报、预期和机构数据 |
| `TAVILY_API_KEYS` / `SERPAPI_API_KEYS` | 新闻搜索 |
| `SOCIAL_SENTIMENT_API_KEY_FILE` | 美股社交舆情服务的本地密钥文件 |
| `V2_SEC_USER_AGENT` | SEC EDGAR 请求标识 |

通知渠道字段见 `.env.notifications.example`，只需配置实际使用的渠道。

## 测试

```bash
python -m pytest tests -q
python -m pytest v2/tests -q
```

## License

[MIT License](LICENSE)

## 免责声明

本项目仅供信息整理和研究使用，不构成投资建议。第三方数据可能存在延迟、限流或字段变化，正式使用前请核对数据口径和授权条款。
