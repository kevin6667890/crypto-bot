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
          <summary><span><b>Advanced Research Tools</b><small>Manual backtests, diagnostics, historical discovery, and parameter experiments.</small></span><i>Collapsed by default</i></summary>
          <p className="advanced-manual-research-note">These tools are for reproduction and investigation. They do not represent the Active Strategy and cannot bypass Automatic Research or the Approved Registry.</p>
          <StrategyResearch />
        </details>
        <details className="advanced-manual-research" open={routerOpen} onToggle={(event) => toggleRouter(event.currentTarget.open)}>
          <summary><span><b>Advanced Strategy Router</b><small>Legacy routing and comparison workspace; it is not a current strategy selection.</small></span><i>Open workspace</i></summary>
          {routerOpen && <Suspense fallback={<div className="route-loading" role="status">{t("common.loading")}</div>}><StrategyRouterResearch instrument={instrument} /></Suspense>}
        </details>
      </div>
    </div>
  );
}
