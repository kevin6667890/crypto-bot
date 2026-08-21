import type { Language } from "./i18n";

type ResearchCopy = {
  eyebrow: string; latest: string; modes: string; history: string; historyHint: string;
  loading: string; empty: string; unavailable: string; auditPassed: string; auditHidden: string;
  current: string; stale: string; requestFailed: string; openReport: string; longTermLevels: string;
};

export const researchPresentationCopy: Record<Language, ResearchCopy> = {
  zh: {
    eyebrow: "AI \u6df1\u5ea6\u4e2d\u5fc3 \u00b7 \u5ba1\u8ba1\u62a5\u544a", latest: "\u6700\u65b0\u5206\u6790", modes: "QUICK \u00b7 FULL \u00b7 POSITION\uff08\u53ef\u7528\u65f6\uff09", history: "\u5386\u53f2\u62a5\u544a", historyHint: "\u5386\u53f2\u62a5\u544a\u4e0d\u4f1a\u66ff\u4ee3\u5f53\u524d\u5e02\u573a\u72b6\u6001", loading: "\u6b63\u5728\u52a0\u8f7d\u5ba1\u8ba1\u62a5\u544a\u2026", empty: "\u6682\u65e0\u5ba1\u8ba1\u901a\u8fc7\u7684\u62a5\u544a\u8bb0\u5f55\u3002", unavailable: "\u8be5\u62a5\u544a\u4e0d\u53ef\u5c55\u793a", auditPassed: "\u5ba1\u8ba1\u901a\u8fc7", auditHidden: "\u5ba1\u8ba1\u672a\u901a\u8fc7\uff0c\u6b63\u6587\u5df2\u9690\u85cf", current: "\u5f53\u524d\u6709\u6548\u62a5\u544a", stale: "\u5386\u53f2\u62a5\u544a \u00b7 \u5df2\u8fc7\u671f", requestFailed: "\u62a5\u544a\u52a0\u8f7d\u5931\u8d25\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5\u3002", openReport: "\u6253\u5f00 AI \u62a5\u544a", longTermLevels: "\u957f\u671f\u53c2\u8003\u4f4d\u7f6e",
  },
  en: {
    longTermLevels: "Long-term reference levels",
    eyebrow: "AI Deep Research · Audited reports", latest: "Latest Analysis", modes: "QUICK · FULL · POSITION (when available)", history: "History", historyHint: "Historical reports do not replace the current market state", loading: "Loading audited reports…", empty: "No audit-passed reports are available.", unavailable: "This report is unavailable for display", auditPassed: "Audit passed", auditHidden: "Audit did not pass; report body is hidden", current: "Current report", stale: "Historical report · expired", requestFailed: "Report loading failed. Please try again.", openReport: "Open AI report",
  },
};

type ResearchReportCandidate = { report_id: string; instrument: string; display_eligible: boolean; status: string };
export function selectResearchReport<T extends ResearchReportCandidate>(items: T[], instrument: string, routeReportId: string): T | null {
  const eligible = items.filter((item) => item.instrument === instrument && item.display_eligible);
  if (routeReportId) return eligible.find((item) => item.report_id === routeReportId) || null;
  return eligible.find((item) => item.status === "CURRENT_AUDITED_REPORT") || eligible[0] || null;
}
