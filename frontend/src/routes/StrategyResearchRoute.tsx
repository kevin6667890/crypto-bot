import { lazy, Suspense, useEffect, useState } from "react";
import StrategyResearch from "../StrategyResearch";
import DiscoveryLab from "../DiscoveryLab";
import { useLanguage } from "../i18n";
import { AiReportResearch } from "../AiReportPresentation";

const StrategyRouterResearch = lazy(() => import("../StrategyRouterResearch"));
type ResearchView = "overview" | "router";

export default function StrategyResearchRoute({
  instrument,
  initialView = "overview",
  onViewChange,
}: {
  instrument: string;
  initialView?: ResearchView;
  onViewChange?: (view: ResearchView) => void;
}) {
  const { language, t } = useLanguage();
  const manual = language === "zh"
    ? {
        title: "高级手动研究",
        description: "用于单策略复现、调试、参数实验与手动历史验证。",
        open: "打开工作区",
        notice: "这里不代表当前策略，也不能绕过自动研究或获批注册表。",
        routerTitle: "高级策略路由",
        routerDescription: "旧版路由与对比工作区；这里不代表当前策略选择。",
      }
    : {
        title: "Advanced manual research",
        description: "For single-strategy reproduction, debugging, parameter experiments, and manual historical validation.",
        open: "Open workspace",
        notice: "This does not represent the active strategy and cannot bypass automatic research or the approved registry.",
        routerTitle: "Advanced Strategy Router",
        routerDescription: "Legacy routing and comparison workspace; it is not a current strategy selection.",
      };
  const [routerOpen, setRouterOpen] = useState(initialView === "router");
  useEffect(() => setRouterOpen(initialView === "router"), [initialView]);
  const toggleRouter = (open: boolean) => {
    setRouterOpen(open);
    onViewChange?.(open ? "router" : "overview");
  };
  return (
    <div className="research-route">
      <header className="workflow-heading">
        <div><span className="eyebrow">RESEARCH EVIDENCE</span><h1>{t("research.workflowTitle")}</h1><p>AI interpretation, automatic research evidence, and the approved registry are shown first. Manual tools remain available below without changing the active paper strategy.</p></div>
      </header>
      <div data-research-view="overview">
        <section className="research-primary-label"><span className="eyebrow">AI INTERPRETATION · AI DEEP CENTER</span><p>Audited AI presentation only; it does not replace raw observations or deterministic interpretation.</p></section>
        <AiReportResearch instrument={instrument} />
        <DiscoveryLab />
        <details className="advanced-manual-research">
          <summary><span><b>{manual.title}</b><small>{manual.description}</small></span><i>{manual.open}</i></summary>
          <p className="advanced-manual-research-note">{manual.notice}</p>
          <StrategyResearch />
        </details>
        <details className="advanced-manual-research" open={routerOpen} onToggle={(event) => toggleRouter(event.currentTarget.open)}>
          <summary><span><b>{manual.routerTitle}</b><small>{manual.routerDescription}</small></span><i>{manual.open}</i></summary>
          {routerOpen && <Suspense fallback={<div className="route-loading" role="status">{t("common.loading")}</div>}><StrategyRouterResearch instrument={instrument} /></Suspense>}
        </details>
      </div>
    </div>
  );
}
