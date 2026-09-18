"""Rule-based macro surprise scenarios and sector sensitivities."""

from __future__ import annotations

import re
from typing import Dict, List, Optional


SCENARIOS: Dict[str, List[dict]] = {
    "cpi": [
        {
            "name": "通胀高于预期",
            "condition": "总体或核心 CPI 明显高于市场一致预期",
            "rates_usd": "美债收益率与美元倾向上行，降息预期后移",
            "broad_market": "估值承压，成长股波动通常更大",
            "positive_sectors": ["金融（短线、视曲线而定）", "能源/资源（若由商品推动）"],
            "negative_sectors": ["科技/半导体", "房地产/REITs", "公用事业", "高估值可选消费"],
        },
        {
            "name": "大致符合预期",
            "condition": "总体与核心 CPI 接近一致预期，分项无明显意外",
            "rates_usd": "利率与美元反应有限，市场回到盈利和个股逻辑",
            "broad_market": "指数可能震荡，分项结构决定日内方向",
            "positive_sectors": ["盈利确定性较高的行业"],
            "negative_sectors": ["缺少基本面支撑的纯估值交易"],
        },
        {
            "name": "通胀低于预期",
            "condition": "总体与核心 CPI 均低于预期，住房/服务通胀同步降温",
            "rates_usd": "美债收益率与美元倾向下行，降息预期升温",
            "broad_market": "风险偏好通常改善，但需排除需求突然恶化",
            "positive_sectors": ["科技/半导体", "房地产/REITs", "公用事业", "可选消费"],
            "negative_sectors": ["美元受益板块", "部分银行（净息差担忧）"],
        },
    ],
    "ppi": [
        {
            "name": "生产端价格高于预期",
            "condition": "PPI 或核心 PPI 明显高于预期",
            "rates_usd": "通胀预期和收益率倾向上行",
            "broad_market": "企业利润率担忧上升，定价能力成为分化核心",
            "positive_sectors": ["能源", "原材料", "具备强定价权的龙头"],
            "negative_sectors": ["零售", "运输", "低毛利制造", "高估值科技"],
        },
        {
            "name": "大致符合预期",
            "condition": "PPI 接近一致预期",
            "rates_usd": "利率反应通常有限",
            "broad_market": "关注 CPI 传导和公司毛利率指引",
            "positive_sectors": ["成本稳定且盈利可见度高的行业"],
            "negative_sectors": ["成本波动敏感但缺乏定价权的行业"],
        },
        {
            "name": "生产端价格低于预期",
            "condition": "PPI 明显低于预期且非需求断崖造成",
            "rates_usd": "收益率倾向下行，通胀压力缓和",
            "broad_market": "利润率和估值环境改善",
            "positive_sectors": ["零售", "运输", "制造", "科技/半导体", "REITs"],
            "negative_sectors": ["上游商品生产商（若由价格下跌驱动）"],
        },
    ],
    "nonfarm_payrolls": [
        {
            "name": "就业与工资显著强于预期",
            "condition": "新增非农和平均时薪同时高于预期、失业率不升",
            "rates_usd": "收益率与美元倾向上行，降息预期后移",
            "broad_market": "经济韧性利好周期股，但长久期估值承压",
            "positive_sectors": ["金融", "工业", "可选消费（需求韧性）"],
            "negative_sectors": ["科技/半导体（估值端）", "REITs", "公用事业"],
        },
        {
            "name": "温和就业/软着陆",
            "condition": "新增就业温和、工资降温、失业率稳定",
            "rates_usd": "利率可能温和下行，美元反应有限",
            "broad_market": "通常是风险资产较友好的“金发姑娘”组合",
            "positive_sectors": ["科技", "可选消费", "工业", "REITs"],
            "negative_sectors": ["纯防御交易相对落后"],
        },
        {
            "name": "就业明显弱于预期",
            "condition": "新增就业大幅低于预期或失业率明显跳升",
            "rates_usd": "收益率倾向下行，降息预期升温",
            "broad_market": "若只是温和降温可利好估值；若接近衰退信号则先风险厌恶",
            "positive_sectors": ["国债敏感资产", "公用事业", "必需消费", "医疗"],
            "negative_sectors": ["银行", "工业", "可选消费", "小盘股"],
        },
    ],
}


CATEGORY_SCENARIOS: Dict[str, List[dict]] = {
    "inflation": [
        {
            **SCENARIOS["cpi"][0],
            "condition": "总体或核心通胀指标明显高于市场一致预期",
        },
        {
            **SCENARIOS["cpi"][1],
            "condition": "总体与核心通胀指标接近一致预期，分项无明显意外",
        },
        {
            **SCENARIOS["cpi"][2],
            "condition": "总体与核心通胀指标低于预期，服务通胀同步降温",
        },
    ],
    "labor_strength": SCENARIOS["nonfarm_payrolls"],
    "labor_weakness": [
        {
            "name": "劳动力闲置高于预期",
            "condition": "失业率或申请失业救济人数高于一致预期",
            "rates_usd": "收益率通常下行；若恶化剧烈，衰退交易升温",
            "broad_market": "温和降温利好估值，明显恶化则压低风险偏好",
            "positive_sectors": ["公用事业", "必需消费", "医疗", "利率敏感资产"],
            "negative_sectors": ["银行", "工业", "可选消费", "小盘股"],
        },
        {
            "name": "大致符合预期",
            "condition": "劳动力闲置指标接近一致预期",
            "rates_usd": "利率与美元反应通常有限",
            "broad_market": "市场回到盈利与其他宏观线索",
            "positive_sectors": ["盈利确定性较高的行业"],
            "negative_sectors": ["缺少基本面支撑的纯宏观交易"],
        },
        {
            "name": "劳动力闲置低于预期",
            "condition": "失业率或申请失业救济人数低于一致预期",
            "rates_usd": "收益率可能上行，降息预期后移",
            "broad_market": "经济韧性改善，但高估值资产承受利率压力",
            "positive_sectors": ["金融", "工业", "可选消费"],
            "negative_sectors": ["REITs", "公用事业", "高估值科技"],
        },
    ],
    "growth": [
        {
            "name": "增长高于预期",
            "condition": "经济活动数据高于一致预期",
            "rates_usd": "收益率与美元可能上行",
            "broad_market": "周期与盈利预期改善，但长久期估值可能承压",
            "positive_sectors": ["工业", "金融", "可选消费", "运输"],
            "negative_sectors": ["公用事业", "REITs", "高估值成长股（利率端）"],
        },
        {
            "name": "大致符合预期",
            "condition": "经济活动数据接近一致预期",
            "rates_usd": "利率反应有限",
            "broad_market": "指数影响较小，市场回到企业盈利",
            "positive_sectors": ["盈利兑现度高的行业"],
            "negative_sectors": ["基本面较弱的高波动交易"],
        },
        {
            "name": "增长低于预期",
            "condition": "经济活动数据低于一致预期",
            "rates_usd": "收益率可能下行，美元偏弱",
            "broad_market": "温和降温可能利好估值，显著走弱则触发衰退担忧",
            "positive_sectors": ["公用事业", "必需消费", "医疗", "优质长久期资产"],
            "negative_sectors": ["工业", "金融", "可选消费", "小盘股"],
        },
    ],
    "survey": [],
    "housing": [
        {
            "name": "住房数据高于预期",
            "condition": "房价、销售或开工数据高于一致预期",
            "rates_usd": "住房韧性和居住通胀黏性可能推高收益率",
            "broad_market": "地产链盈利预期改善，但降息预期可能后移",
            "positive_sectors": ["住宅建筑商", "建材", "家居", "部分银行"],
            "negative_sectors": ["REITs", "公用事业", "高估值成长股（利率端）"],
        },
        {
            "name": "大致符合预期",
            "condition": "住房数据接近一致预期",
            "rates_usd": "利率反应通常有限，继续关注按揭利率与库存",
            "broad_market": "对大盘影响有限，地产链回到供需和公司盈利",
            "positive_sectors": ["基本面稳健的住宅与家居公司"],
            "negative_sectors": ["高杠杆且库存压力较大的地产链公司"],
        },
        {
            "name": "住房数据低于预期",
            "condition": "房价、销售或开工数据低于一致预期",
            "rates_usd": "居住通胀与利率压力可能缓和；显著走弱时需防衰退交易",
            "broad_market": "地产链需求预期承压，但利率敏感资产可能受益于收益率回落",
            "positive_sectors": ["REITs", "公用事业", "优质长久期资产"],
            "negative_sectors": ["住宅建筑商", "建材", "家居", "部分银行"],
        },
    ],
    "central_bank": [
        {
            "name": "政策/表态偏鹰",
            "condition": "政策利率、点阵图或措辞比一致预期更鹰派",
            "rates_usd": "收益率与美元倾向上行",
            "broad_market": "估值承压，利率敏感板块波动放大",
            "positive_sectors": ["部分金融", "美元受益板块"],
            "negative_sectors": ["科技/半导体", "REITs", "公用事业", "高估值可选消费"],
        },
        {
            "name": "政策大致符合预期",
            "condition": "决定与沟通接近市场定价",
            "rates_usd": "主要资产反应有限，关注记者会细节",
            "broad_market": "市场回到盈利和后续数据",
            "positive_sectors": ["盈利确定性较高的行业"],
            "negative_sectors": ["依赖单一政策押注的交易"],
        },
        {
            "name": "政策/表态偏鸽",
            "condition": "政策利率、点阵图或措辞比一致预期更鸽派",
            "rates_usd": "收益率与美元倾向下行",
            "broad_market": "估值环境改善，但需判断鸽派是否源于增长恶化",
            "positive_sectors": ["科技/半导体", "REITs", "公用事业", "可选消费"],
            "negative_sectors": ["部分银行", "美元受益板块"],
        },
    ],
    "energy_inventory": [
        {
            "name": "库存高于预期",
            "condition": "原油、成品油或天然气库存增幅高于预期",
            "rates_usd": "对总利率影响通常有限",
            "broad_market": "商品价格可能承压，影响能源链盈利预期",
            "positive_sectors": ["航空", "运输", "能源密集型制造"],
            "negative_sectors": ["油气开采", "油服", "部分能源设备"],
        },
        {
            "name": "大致符合预期",
            "condition": "库存变化接近一致预期",
            "rates_usd": "宏观影响有限",
            "broad_market": "能源价格更受供给事件与需求预期驱动",
            "positive_sectors": ["成本和产量指引清晰的公司"],
            "negative_sectors": ["缺少基本面催化的商品交易"],
        },
        {
            "name": "库存低于预期",
            "condition": "库存增幅低于预期或去库强于预期",
            "rates_usd": "若推升油价，可能抬高通胀预期",
            "broad_market": "能源价格和能源股可能走强，下游成本压力增加",
            "positive_sectors": ["油气开采", "油服", "能源设备"],
            "negative_sectors": ["航空", "运输", "高耗能制造"],
        },
    ],
    "rates": [],
    "fiscal": [],
    "other": [],
}

# Survey releases share the same basic activity-surprise transmission.
for _category in ("survey",):
    CATEGORY_SCENARIOS[_category] = CATEGORY_SCENARIOS["growth"]
for _category in ("rates", "fiscal", "other"):
    CATEGORY_SCENARIOS[_category] = [
        {
            "name": "高于预期/偏紧",
            "condition": "实际结果高于一致预期，具体方向需结合指标定义",
            "rates_usd": "利率与美元影响依指标性质判断",
            "broad_market": "先核对指标定义，再判断增长、通胀或流动性传导",
            "positive_sectors": ["与该指标方向一致的行业"],
            "negative_sectors": ["对该指标变化敏感的反向行业"],
        },
        {
            "name": "大致符合预期",
            "condition": "实际结果接近一致预期",
            "rates_usd": "市场反应通常有限",
            "broad_market": "通常不改变原有市场主线",
            "positive_sectors": ["基本面确定性高的行业"],
            "negative_sectors": ["纯事件博弈"],
        },
        {
            "name": "低于预期/偏松",
            "condition": "实际结果低于一致预期，具体方向需结合指标定义",
            "rates_usd": "利率与美元影响依指标性质判断",
            "broad_market": "先核对指标定义，再判断增长、通胀或流动性传导",
            "positive_sectors": ["与该指标方向一致的行业"],
            "negative_sectors": ["对该指标变化敏感的反向行业"],
        },
    ]


def scenario_probabilities(history_counts: Optional[List[int]] = None) -> dict:
    """Return transparent scenario weights, using a neutral prior until history is sufficient."""
    counts = list(history_counts or [])
    if len(counts) == 3 and sum(max(0, int(item)) for item in counts) >= 12:
        posterior = [max(0, int(item)) + prior for item, prior in zip(counts, (3, 4, 3))]
        total = sum(posterior)
        percentages = [round(item * 100 / total) for item in posterior]
        percentages[1] += 100 - sum(percentages)
        return {
            "percentages": percentages,
            "basis": "historical_surprise_frequency_with_prior",
            "sample_size": sum(counts),
        }
    return {
        "percentages": [30, 40, 30],
        "basis": "neutral_prior_not_historically_calibrated",
        "sample_size": sum(max(0, int(item)) for item in counts) if counts else 0,
    }


def scenarios_for(
    event_type: str, category: str = "", history_counts: Optional[List[int]] = None
) -> List[dict]:
    items = SCENARIOS.get(event_type) or CATEGORY_SCENARIOS.get(category) or CATEGORY_SCENARIOS["other"]
    probability = scenario_probabilities(history_counts)
    result = []
    for index, item in enumerate(items):
        result.append(
            {
                **dict(item),
                "probability_pct": probability["percentages"][index],
                "probability_basis": probability["basis"],
                "probability_sample_size": probability["sample_size"],
            }
        )
    return result


def _number(value: object) -> Optional[float]:
    text = str(value or "").strip().replace(",", "")
    if not text or text.lower() in {"n/a", "na", "none", "-", "—"}:
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    number = float(match.group(0))
    suffix = text[match.end() :].strip().upper()[:1]
    number *= {"K": 1_000.0, "M": 1_000_000.0, "B": 1_000_000_000.0, "T": 1_000_000_000_000.0}.get(suffix, 1.0)
    return number


def analyze_release(
    *, event_type: str, category: str, actual: object, estimate: object, previous: object
) -> dict:
    actual_number = _number(actual)
    estimate_number = _number(estimate)
    previous_number = _number(previous)
    if actual_number is None:
        return {"status": "scheduled", "summary": "尚未公布实际值。"}

    basis = ""
    comparison = "unavailable"
    reference_number = None
    reference_label = ""
    if estimate_number is not None:
        reference_number = estimate_number
        reference_label = "市场预期"
        basis = "consensus"
    elif previous_number is not None:
        reference_number = previous_number
        reference_label = "前值（无一致预期）"
        basis = "previous"

    if reference_number is not None:
        tolerance = max(abs(reference_number) * 0.005, 1e-9)
        delta = actual_number - reference_number
        comparison = "above" if delta > tolerance else "below" if delta < -tolerance else "inline"
    scenario_index = {"above": 0, "inline": 1, "below": 2}.get(comparison, 1)
    scenario = scenarios_for(event_type, category)[scenario_index]
    comparison_label = {"above": "高于", "inline": "大致符合", "below": "低于"}.get(comparison, "无法比较")
    summary = (
        f"实际值 {actual}，{comparison_label}{reference_label} {estimate if basis == 'consensus' else previous}。"
        if reference_number is not None
        else f"实际值 {actual}；缺少一致预期与可比前值，暂不判断意外方向。"
    )
    return {
        "status": "released",
        "comparison": comparison,
        "comparison_basis": basis,
        "summary": summary,
        "scenario_name": scenario.get("name"),
        "broad_market": scenario.get("broad_market"),
        "rates_usd": scenario.get("rates_usd"),
        "positive_sectors": list(scenario.get("positive_sectors", [])),
        "negative_sectors": list(scenario.get("negative_sectors", [])),
        "caution": "这是基于公布值相对预期的第一层映射，仍需结合分项、修订值及市场即时定价。",
    }
