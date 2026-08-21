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
  const { t } = useLanguage();
  const [view, setView] = useState<ResearchView>(initialView);
  useEffect(() => setView(initialView), [initialView]);
  const selectView = (next: ResearchView) => { setView(next); onViewChange?.(next); };
  return (
    <div className="research-route">
      <header className="workflow-heading">
        <div><span className="eyebrow">{t("research.workflowEyebrow")}</span><h1>{t("research.workflowTitle")}</h1><p>{t("research.workflowDescription")}</p></div>
        <nav className="secondary-tabs" aria-label={t("research.workflowNav")}>
          <button className={view === "overview" ? "active" : ""} onClick={() => selectView("overview")}>{t("research.overviewTab")}</button>
          <button className={view === "router" ? "active" : ""} onClick={() => selectView("router")}>{t("research.routerTab")}</button>
        </nav>
      </header>
      <div hidden={view !== "overview"} data-research-view="overview">
        <AiReportResearch instrument={instrument} />
        <DiscoveryLab />
        <details className="advanced-manual-research">
          <summary><span><b>Advanced Manual Research</b><small>For single-strategy reproduction, debugging, parameter experiments, and manual historical validation.</small></span><i>Open workspace</i></summary>
          <p className="advanced-manual-research-note">This does not represent the Active Strategy and cannot bypass Automatic Research or the Approved Registry.</p>
          <StrategyResearch />
        </details>
      </div>
      {view === "router" && <Suspense fallback={<div className="route-loading" role="status">{t("common.loading")}</div>}><StrategyRouterResearch instrument={instrument} /></Suspense>}
    </div>
  );
}
