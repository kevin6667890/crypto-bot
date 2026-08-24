import type { Language } from "../i18n";

const copy = {
  en: {
    title: "Test an idea", subtitle: "Turn a market hypothesis into a reproducible historical test.",
    placeholder: "When BTC 4H volume ratio is at least 1.2 and price is above MA200, what happened over the next 4H, 12H and 24H historically?",
    interpret: "Interpret idea", interpreting: "Interpreting your idea…", testing: "Testing historical events…",
    understood: "I understood your idea as", conditions: "Required conditions", outcomes: "Forward outcomes",
    run: "Run historical test", needs: "I need one more parameter", unsupported: "This idea cannot be tested exactly yet.",
    unsupportedList: "Currently unsupported", noResult: "No historical result has been produced.",
    aiUnavailable: "AI interpretation is unavailable. You can still build the same deterministic test manually.",
    manual: "Build manually", add: "Add condition", remove: "Remove", removed: "Condition removed by user.",
    evidence: "Historical evidence", independent: "Independent events", sample: "Sample quality", tested: "Tested",
    coverage: "Coverage", limitations: "Limitations", matches: "Historical matches", horizon: "Horizon",
    positive: "Historical positive rate", median: "Median return", p25: "P25", p75: "P75", mfe: "Median MFE", mae: "Median MAE",
    usable: "usable", censored: "censored", reference: "Reference close", status: "Status", examples: "Try a real supported example",
    apiError: "Historical test could not be completed.", parseError: "Your idea could not be interpreted.", retry: "Try again",
    empty: "Enter an idea before interpreting it.", noMatches: "No independent historical events matched this definition.",
    notTestable: "This thesis cannot be tested as requested. Required historical coverage did not qualify.",
    back: "Back to workspace", definition: "Definition", true: "True", threshold: "Explicit threshold required",
    showing: "Showing the latest {shown} of {total} provenance records.", historicalFrame: "Historical event study — not a prediction or trading recommendation.",
  },
  zh: {
    title: "测试一个想法", subtitle: "把市场假设转化为可复现的历史检验。",
    placeholder: "当 BTC 4H 成交量比率至少为 1.2 且价格高于 MA200 时，之后 4H、12H、24H 历史上发生了什么？",
    interpret: "解析想法", interpreting: "正在解析你的想法…", testing: "正在检验历史事件…",
    understood: "系统将你的想法理解为", conditions: "必要条件", outcomes: "前瞻结果周期",
    run: "运行历史检验", needs: "还需要一个明确参数", unsupported: "目前无法精确检验这个想法。",
    unsupportedList: "当前不支持", noResult: "未生成任何历史结果。",
    aiUnavailable: "AI 解析暂不可用。你仍可手动构建同一个确定性检验。",
    manual: "手动构建", add: "添加条件", remove: "删除", removed: "条件已由用户明确删除。",
    evidence: "历史证据", independent: "独立事件", sample: "样本质量", tested: "检验区间",
    coverage: "数据覆盖", limitations: "限制", matches: "历史匹配事件", horizon: "周期",
    positive: "历史正收益比例", median: "收益中位数", p25: "P25", p75: "P75", mfe: "MFE 中位数", mae: "MAE 中位数",
    usable: "可用", censored: "已删失", reference: "参考收盘价", status: "状态", examples: "尝试真实支持的示例",
    apiError: "历史检验未能完成。", parseError: "无法解析你的想法。", retry: "重试",
    empty: "请先输入一个想法。", noMatches: "没有独立历史事件匹配这个定义。",
    notTestable: "无法按当前定义完成检验：必要历史数据覆盖未达到要求。",
    back: "返回工作区", definition: "确定性定义", true: "真", threshold: "需要明确阈值",
    showing: "显示最近 {shown} 条，共 {total} 条溯源记录。", historicalFrame: "历史事件研究——不是预测或交易建议。",
  },
} as const;

export function thesisText(language: Language) { return copy[language]; }
