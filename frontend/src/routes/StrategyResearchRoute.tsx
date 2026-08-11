import { lazy, Suspense, useEffect, useState } from "react";
import StrategyResearch from "../StrategyResearch";
import DiscoveryLab from "../DiscoveryLab";
import { useLanguage } from "../i18n";

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
      <div hidden={view !== "overview"} data-research-view="overview"><StrategyResearch /><DiscoveryLab /></div>
      {view === "router" && <Suspense fallback={<div className="route-loading" role="status">{t("common.loading")}</div>}><StrategyRouterResearch instrument={instrument} /></Suspense>}
    </div>
  );
}
