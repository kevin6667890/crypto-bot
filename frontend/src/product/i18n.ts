import type { Language } from "../i18n";

const copy = {
  en: {
    brand: "Evidence workspace", navigation: "Product navigation", language: "Language",
    home: "Home", test: "Test an idea", tracking: "Tracking", changed: "What changed", advanced: "Advanced",
    eyebrow: "Reproducible crypto market research", hero: "Evidence, not predictions.",
    subtitle: "Turn a crypto market hypothesis into a reproducible historical test, then revisit it as current evidence changes.",
    start: "Test an idea", seeChanges: "See what changed", entries: "Three ways to use the evidence",
    changedTitle: "What changed?", changedBody: "See material changes in current evidence—not market noise.",
    testTitle: "Test an idea", testBody: "Turn a market hypothesis into an auditable historical event study.",
    trackingTitle: "What am I tracking?", trackingBody: "Return to saved definitions and see whether current conditions still match.",
    recent: "Recent evidence changes", noRecent: "No material evidence changes since the last confirmed update.", viewAll: "View all changes",
  },
  zh: {
    brand: "证据研究", navigation: "产品导航", language: "语言",
    home: "首页", test: "测试想法", tracking: "跟踪", changed: "发生了什么变化", advanced: "高级功能",
    eyebrow: "可复现的加密市场研究", hero: "用证据，而不是预测。",
    subtitle: "把市场假设变成可复现的历史检验，并在当前证据变化后回来复查。",
    start: "测试一个想法", seeChanges: "查看证据变化", entries: "从三个问题开始",
    changedTitle: "发生了什么变化？", changedBody: "查看当前证据的实质变化，而不是行情噪声。",
    testTitle: "测试一个想法", testBody: "把市场假设变成可审计的历史事件研究。",
    trackingTitle: "我在跟踪什么？", trackingBody: "回看已保存的定义，了解当前条件是否仍然匹配。",
    recent: "近期证据变化", noRecent: "自上次确认更新后，没有实质证据变化。", viewAll: "查看全部变化",
  },
} as const;

export function productText(language: Language) { return copy[language]; }
