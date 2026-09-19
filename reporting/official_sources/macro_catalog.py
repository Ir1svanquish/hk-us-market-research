"""Normalize a broad US macro calendar into stable categories and importance levels."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class MacroClassification:
    event_type: str
    category: str
    importance: str


# Order matters: specific/core series must be matched before their broader family.
RULES = [
    (r"(?:fed|fomc) .* speaks|federal reserve .* speech|beige book|fomc minutes", "fed_communication", "central_bank", "medium"),
    (r"fomc|federal funds|interest rate decision|fed rate", "fomc", "central_bank", "high"),
    (r"core pce|core personal consumption", "core_pce", "inflation", "high"),
    (r"pce price|personal consumption expenditure.*price", "pce", "inflation", "high"),
    (r"core cpi", "core_cpi", "inflation", "high"),
    (r"consumer price index|\bcpi\b", "cpi", "inflation", "high"),
    (r"core ppi", "core_ppi", "inflation", "high"),
    (r"producer price index|\bppi\b", "ppi", "inflation", "high"),
    (r"import price|export price", "import_export_prices", "inflation", "medium"),
    (r"employment cost index|unit labor cost|average hourly earnings|wage", "labor_costs", "inflation", "high"),
    (r"nonfarm payroll|employment situation|private payroll|manufacturing payroll", "nonfarm_payrolls", "labor_strength", "high"),
    (r"unemployment rate|continuing jobless|initial jobless|jobless claims", "labor_slack", "labor_weakness", "high"),
    (r"job openings|jolts", "jolts", "labor_strength", "high"),
    (r"adp employment", "adp_employment", "labor_strength", "medium"),
    (r"challenger job cuts|layoff", "job_cuts", "labor_weakness", "medium"),
    (r"labor force participation|employment change|employment index", "labor_other", "labor_strength", "medium"),
    (r"gross domestic product|\bgdp\b|corporate profits", "gdp", "growth", "high"),
    (r"retail sales", "retail_sales", "growth", "high"),
    (r"durable goods", "durable_goods", "growth", "high"),
    (r"industrial production|capacity utilization", "industrial_production", "growth", "medium"),
    (r"factory orders|business inventories|wholesale inventories", "orders_inventories", "growth", "medium"),
    (r"trade balance|current account|goods trade", "trade_balance", "growth", "medium"),
    (r"productivity", "productivity", "growth", "medium"),
    (r"ism manufacturing|manufacturing pmi", "manufacturing_pmi", "survey", "high"),
    (r"ism services|services pmi|composite pmi", "services_pmi", "survey", "high"),
    (r"consumer confidence|consumer sentiment|michigan sentiment", "consumer_sentiment", "survey", "high"),
    (r"philadelphia fed|philly fed|empire state|richmond fed|chicago pmi|kansas city fed|dallas fed", "regional_survey", "survey", "medium"),
    (r"nfib|small business optimism", "small_business", "survey", "medium"),
    (r"building permits|housing starts", "housing_construction", "housing", "high"),
    (r"existing home sales|new home sales|pending home sales", "home_sales", "housing", "medium"),
    (r"home price|house price|case-shiller", "home_prices", "housing", "medium"),
    (r"mortgage application|mortgage market", "mortgage_activity", "housing", "low"),
    (r"crude oil inventories|eia crude|gasoline inventories|distillate", "oil_inventories", "energy_inventory", "medium"),
    (r"natural gas storage|natural gas stocks", "gas_inventories", "energy_inventory", "medium"),
    (r"treasury auction|bill auction|note auction|bond auction|tic net", "rates_flows", "rates", "medium"),
    (r"government budget|federal budget", "fiscal_balance", "fiscal", "medium"),
    (r"construction spending", "construction_spending", "growth", "medium"),
    (r"personal income|personal spending", "personal_income_spending", "growth", "high"),
    (r"real earnings", "real_earnings", "growth", "medium"),
]


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return f"macro_{slug[:64]}" if slug else "macro_other"


def classify_macro_event(title: str) -> MacroClassification:
    normalized = re.sub(r"\s+", " ", str(title or "")).strip().lower()
    for pattern, event_type, category, importance in RULES:
        if re.search(pattern, normalized, re.IGNORECASE):
            return MacroClassification(event_type, category, importance)
    return MacroClassification(_slug(normalized), "other", "low")


TITLE_TRANSLATIONS = {
    "S&P Global Manufacturing PMI": "标普全球制造业 PMI",
    "S&P Global Composite PMI": "标普全球综合 PMI",
    "S&P Global Services PMI": "标普全球服务业 PMI",
    "Building Permits": "建筑许可",
    "House Price Index": "FHFA 房价指数",
    "New Home Sales": "新屋销售",
    "S&P/CS HPI Composite - 20 n.s.a.": "标普/Case-Shiller 20城房价指数（未季调）",
    "S&P/CS HPI Composite - 20 s.a.": "标普/Case-Shiller 20城房价指数（季调）",
    "CB Consumer Confidence": "美国咨商会消费者信心指数",
    "Core Durable Goods Orders": "核心耐用品订单",
    "Durable Goods Orders": "耐用品订单",
    "Corporate Profits": "企业利润",
    "GDP": "国内生产总值（GDP）",
    "GDP Price Index": "GDP 价格指数",
    "GDP Sales": "GDP 最终销售",
    "Core PCE Price Index": "核心 PCE 物价指数",
    "Core PCE Prices": "核心 PCE 物价",
    "PCE Price index": "PCE 物价指数",
    "PCE Prices": "PCE 物价",
    "Personal Income": "个人收入",
    "Personal Spending": "个人支出",
    "Continuing Jobless Claims": "续请失业救济人数",
    "Initial Jobless Claims": "初请失业救济人数",
    "Jobless Claims 4-Week Avg.": "初请失业救济四周均值",
    "Philadelphia Fed Manufacturing Index": "费城联储制造业指数",
    "Philly Fed Business Conditions": "费城联储商业状况指数",
    "Philly Fed CAPEX Index": "费城联储资本开支指数",
    "Philly Fed Employment": "费城联储就业指数",
    "Philly Fed New Orders": "费城联储新订单指数",
    "Philly Fed Prices Paid": "费城联储支付价格指数",
    "Crude Oil Inventories": "美国原油库存",
    "Cushing Crude Oil Inventories": "库欣原油库存",
    "Distillate Fuel Production": "馏分油产量",
    "EIA Weekly Distillates Stocks": "EIA 馏分油库存",
    "Gasoline Inventories": "汽油库存",
    "Natural Gas Storage": "天然气库存",
    "20-Year Bond Auction": "美国 20 年期国债拍卖",
    "4-Week Bill Auction": "美国 4 周期国库券拍卖",
    "8-Week Bill Auction": "美国 8 周期国库券拍卖",
    "FOMC Member Barkin Speaks": "美联储巴尔金讲话",
}


def translate_macro_title(title: str) -> str:
    value = str(title or "").strip()
    return TITLE_TRANSLATIONS.get(value, value)


EVENT_IMPACT_SCORE = {
    "fomc": 1000,
    "core_cpi": 995,
    "cpi": 990,
    "core_pce": 985,
    "pce": 980,
    "nonfarm_payrolls": 975,
    "labor_slack": 940,
    "gdp": 930,
    "retail_sales": 920,
    "manufacturing_pmi": 900,
    "services_pmi": 900,
    "jolts": 880,
    "core_ppi": 870,
    "ppi": 865,
    "labor_costs": 850,
    "durable_goods": 835,
    "consumer_sentiment": 820,
    "industrial_production": 760,
    "regional_survey": 730,
    "housing_construction": 620,
    "home_sales": 580,
    "oil_inventories": 540,
    "gas_inventories": 500,
    "fed_communication": 470,
    "rates_flows": 380,
}

CATEGORY_IMPACT_SCORE = {
    "central_bank": 950,
    "inflation": 900,
    "labor_strength": 880,
    "labor_weakness": 860,
    "growth": 800,
    "survey": 700,
    "housing": 550,
    "energy_inventory": 480,
    "rates": 350,
    "fiscal": 320,
    "other": 100,
}


def macro_market_score(event_type: str, category: str) -> int:
    return EVENT_IMPACT_SCORE.get(event_type, CATEGORY_IMPACT_SCORE.get(category, 100))


def simple_macro_explanation(category: str, titles: str) -> str:
    lowered = titles.lower()
    if "pce" in lowered:
        return "PCE 是美联储重点参考的通胀指标；高于预期通常推高利率、压制高估值成长股，低于预期则相反。"
    if "cpi" in lowered or "price" in lowered and category == "inflation":
        return "该数据直接影响通胀和降息预期，通常首先传导到美债收益率，再影响科技、REITs 与金融板块。"
    if "gdp" in lowered:
        return "GDP 衡量整体经济增速；强于预期利好周期股和盈利预期，但也可能让降息预期后移。"
    if "durable" in lowered:
        return "耐用品订单反映企业和居民的大额支出意愿，是制造业需求与资本开支的领先线索。"
    if "jobless" in lowered:
        return "失业救济申请是高频就业温度计；申请减少说明就业仍强，利好周期股但可能推迟降息。"
    if "pmi" in lowered:
        return "PMI 是经济活动的领先指标；高于 50 通常代表扩张，制造业与服务业分化也会影响板块轮动。"
    if "consumer confidence" in lowered:
        return "消费者信心影响未来消费意愿，对零售、旅游、汽车和其他可选消费板块较敏感。"
    if "phil" in lowered:
        return "费城联储调查是制造业领先指标，重点看新订单、就业与支付价格是否同时改善。"
    if category == "energy_inventory":
        return "库存变化会直接影响油气价格和能源股，同时通过能源成本影响运输、航空与通胀预期。"
    if category == "central_bank":
        return "央行决定和措辞会直接改变利率路径，是影响估值、美元和风险偏好的核心变量。"
    if category == "housing":
        return "住房数据对利率非常敏感，可反映房地产需求、建筑活动和相关消费链景气度。"
    if category == "rates":
        return "国债拍卖反映市场对期限供给的承接能力，尾部利率和投标倍数异常时会影响长端收益率。"
    return "该指标用于补充判断增长、通胀或流动性方向，需结合市场预期和相关分项解读。"
