<div align="center">

# 港美股研究报告

> 基于 [daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis) 的港股 / 美股研究增强分支，重点改进官方来源核验、机会评分、交易卡和报告交付。

[**本仓库改动**](#-本仓库改了什么) · [**报告效果**](#-报告效果) · [**快速开始**](#-快速开始) · [**发布范围**](#-当前发布范围)

简体中文 | [English](README_EN.md)

</div>

## ✨ 本仓库改了什么

上游项目已经提供行情获取、模型调用、新闻搜索、决策看板和多渠道通知。本仓库不重复介绍这些通用能力，主要记录港美股报告链路的新增和调整。

| 改动 | 内容 |
| --- | --- |
| 港股专项数据 | 接入 HKEX 公告、董事会日历、南向资金、港元流动性及相关市场变量 |
| 美股官方来源 | 接入 SEC EDGAR、财报窗口、公司指引、Nasdaq 经济日历和 BLS 日程 |
| 社交舆情 | 可选接入 Reddit、X、Polymarket；仅用于美股，并写入分析上下文和正式报告 |
| 机会评分 | 在基础评分之外增加机会强度、数据完整度、信号置信度和执行成熟度 |
| 标准化交易卡 | 给出触发条件、失效条件、止损和目标位；证据不足时只保留观察结论 |
| 跨日状态 | 记录事件和信号在后续交易日的变化，避免每天从零开始判断 |
| 历史验证 | 跟踪 Top 5 信号的 1/5/20 日表现、最大有利/不利波动及目标/止损命中 |
| 报告交付 | 即时通讯渠道发送简报，邮件和附件保留完整 PDF；同一报告不会重复发送 |

基础模型、行情源、搜索服务和通知渠道沿用上游实现，完整说明见 [原项目 README](https://github.com/ZhuLinsen/daily_stock_analysis/blob/main/README.md)。

## 📱 报告效果

### 港美股复盘

报告先说明当日市场状态、主线方向、指数与板块表现，再整理未来宏观事件、财报窗口和需要继续核验的公司事件。港股和美股使用各自的数据源与市场规则。

### 机会评分与交易卡

每只股票保留核心结论、评分、趋势、风险警报和催化因素，并补充数据完整度、信号置信度与执行状态。只有数据和结构同时满足条件时，才会生成可执行交易卡。

### 美股社交舆情

配置舆情数据源后，报告会显示 Reddit、X、Polymarket 的热度、情绪、提及量和预测市场活动。舆情用于补充事件背景，不会单独决定交易结论。

### 推送摘要结构

```text
港股/美股复盘及机会日报｜日期｜市场状态

市场状态 / 当前主线 / 今日策略
重点机会 / 机会评分 / 数据完整度 / 信号置信度
触发条件 / 失效条件 / 止损 / 目标位
风险警报 / 利好催化 / 最新动态
未来宏观事件 / 财报窗口 / 官方公告核验
```

支持 Telegram、邮件、企业微信、飞书、钉钉、Discord、Slack、Pushover、PushPlus、Server酱3、AstrBot 和自定义 Webhook。

## 🚀 快速开始

### 运行环境

Python 主程序支持 Windows、Linux 和 macOS。`run_hk_once.sh`、`run_us_once.sh` 依赖 Bash、`flock` 和 GNU 工具，主要用于 Linux 服务器；Windows 和 macOS 可以直接运行 Python 命令或使用系统自带的定时任务。

PDF 输出依赖 WeasyPrint，系统依赖安装方式见 [官方文档](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html)。

### 安装

Linux / macOS：

```bash
git clone https://github.com/Ir1svanquish/hk-us-market-research.git
cd hk-us-market-research
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Windows PowerShell：

```powershell
git clone https://github.com/Ir1svanquish/hk-us-market-research.git
Set-Location hk-us-market-research
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### 配置

仓库中提交的 `.env.example`、`.env.hk.example`、`.env.us.example` 和 `.env.notifications.example` 是**不含真实凭据的配置模板**，保留它们是为了说明可用字段。

复制模板后再填写自己的配置：

```bash
cp .env.hk.example .env.hk
cp .env.us.example .env.us
```

Windows PowerShell：

```powershell
Copy-Item .env.hk.example .env.hk
Copy-Item .env.us.example .env.us
```

填写后的 `.env`、`.env.hk`、`.env.us` 以及本地密钥文件已被 `.gitignore` 排除，**不要手动强制提交**。

本仓库新增或重点使用的配置：

| 配置 | 用途 |
| --- | --- |
| `V2_HK_SYMBOLS` / `V2_US_SYMBOLS` | 港股 / 美股结构化报告股票池 |
| `V2_SEC_USER_AGENT` | SEC EDGAR 请求标识 |
| `FINNHUB_API_KEYS` | 美股财报、预期与机构数据 |
| `LONGBRIDGE_APP_KEY` / `LONGBRIDGE_APP_SECRET` / `LONGBRIDGE_ACCESS_TOKEN` | 港美股实时行情和估值字段 |
| `SOCIAL_SENTIMENT_API_KEY_FILE` | 美股 Reddit、X、Polymarket 舆情服务的本地密钥文件 |
| `LLM_CHANNELS` / `LITELLM_MODEL` | 模型渠道和模型名称 |
| `TAVILY_API_KEYS` / `SERPAPI_API_KEYS` | 新闻搜索，可按需配置其他上游支持的搜索源 |

### 运行

基础分析：

```bash
cp .env.hk .env
python main.py --no-notify
```

Linux 服务器完整流程：

```bash
./run_hk_once.sh
./run_us_once.sh
```

完整流程会先生成基础分析，再生成结构化报告，并通过本地状态避免重复处理和重复发送。

Windows 或 macOS 可在基础报告生成后直接运行结构化报告模块：

```bash
python -m v2.shadow_delivery --market hk --date YYYY-MM-DD --env-file .env.hk --delivery-mode v2
```

美股运行时将 `hk` 和 `.env.hk` 改为 `us` 和 `.env.us`。

## 📦 当前发布范围

当前仓库发布的是命令行分析、定时报告和多渠道推送链路：

- 已接入主流程：决策看板、港美股复盘、官方来源核验、机会评分、交易卡、社交舆情和报告推送
- 已保留核心代码：Agent 分析框架、11 种内置策略、图片识股、CSV/Excel 解析、名称补全和持仓导入
- 暂未提供完整入口：Telegram/Discord 常驻 Bot、Web/API 智能导入界面
- 未包含：上游 Web/桌面工作台、FastAPI 服务、Docker 部署和 GitHub Actions 工作流
- 当前文档只承诺经过验证的港股和美股链路；继承的 A 股和 ETF 代码不属于主要发布范围

## 测试

```bash
python -m pytest tests -q
python -m pytest v2/tests -q
```

## 上游项目与许可

本仓库保留上游 MIT License 和原作者版权声明。通用功能、完整 Web/API 部署及其他市场支持请参考：

- [ZhuLinsen/daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis)
- [原项目完整 README](https://github.com/ZhuLinsen/daily_stock_analysis/blob/main/README.md)

## 免责声明

本项目仅用于信息整理和研究，不构成投资建议。第三方数据可能存在延迟、限流或字段变化，正式使用前请核对数据口径和授权条款。
