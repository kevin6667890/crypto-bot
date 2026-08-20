import { useEffect, useState } from "react";
import type { ApprovedStrategy } from "./data";
import { useLanguage } from "./i18n";

type ResearchCounts = { development_candidates:number; eligible_finalists:number; rejected_candidates:number };
type Cycle = {
  id:number; status:string; research_start:number; research_end:number; created_at:string;
  started_at?:string; completed_at?:string; dataset_fingerprint?:string;
  checkpoint?:Record<string,unknown>; research_counts?:ResearchCounts;
  result?:{ approved?:number; rejected?:number; active_registry_id?:string };
};
type Scheduler = {
  scheduler_name:string; enabled:boolean; interval_hours:number; next_due_at:string;
  last_scheduled_at?:string; last_started_cycle_id?:number;
};
type Summary = {
  latest_cycle:Cycle|null; recent_cycles:Cycle[]; active_strategy:ApprovedStrategy|null;
  approved_strategies:ApprovedStrategy[]; scheduler:Scheduler|null;
  scheduler_enabled:boolean; interval_hours?:number; next_due_at?:string;
};

const day=(timestamp?:number)=>timestamp ? new Date(timestamp*1000).toISOString().slice(0,10) : "—";
const compactHash=(input?:string)=>input ? `${input.slice(0,12)}…` : "—";
const tone=(status?:string)=>{
  const normalized=(status||"").toUpperCase();
  if(["COMPLETED","PASS","ACTIVE","ENABLED"].includes(normalized))return "healthy";
  if(["FAILED","CANCELLED","INTERRUPTED","NO_ELIGIBLE"].includes(normalized))return "warning";
  return "neutral";
};

export default function DiscoveryLab(){
  const {t,value,language}=useLanguage();
  const [summary,setSummary]=useState<Summary|null>(null);
  const [message,setMessage]=useState("");
  const load=()=>{
    setMessage("");
    return fetch("/api/automatic-research").then(async response=>{
      if(!response.ok)throw new Error(`HTTP ${response.status}`);
      return response.json() as Promise<Summary>;
    }).then(setSummary).catch(error=>setMessage(t("autoResearch.unavailable",{error:String(error)})));
  };
  useEffect(()=>{void load();},[]);

  const latest=summary?.latest_cycle,active=summary?.active_strategy,scheduler=summary?.scheduler;
  const counts=latest?.research_counts||{development_candidates:0,eligible_finalists:0,rejected_candidates:0};
  const checkpointStages=Object.keys(latest?.checkpoint||{});
  const progressStage=checkpointStages[checkpointStages.length-1];
  const completed=latest?.status==="COMPLETED";
  const noEligibleCandidate=completed&&counts.development_candidates>0&&counts.eligible_finalists===0;
  const validation=active?.serialized_definition.validation_status||{};
  const validationStages=[
    ["Development",validation.development||(completed?"COMPLETED":latest?.status||"NOT_RUN")],
    ["Walk-forward",validation.walk_forward||(counts.development_candidates?(completed?"COMPLETED":"RUNNING"):"NOT_RUN")],
    ["Holdout",validation.holdout||"NOT_RUN"],["OOT",validation.oot||"NOT_RUN"],
    ["Cross-asset",validation.cross_asset||"NOT_RUN"],["Robustness",validation.robustness||"NOT_RUN"],
  ];
  const formatTime=(timestamp?:string)=>timestamp
    ? new Intl.DateTimeFormat(language==="zh"?"zh-CN":"en-GB",{dateStyle:"medium",timeStyle:"short",timeZone:"UTC"}).format(new Date(timestamp))+" UTC"
    : "—";
  const interval=summary?.interval_hours||scheduler?.interval_hours;

  return <section className="panel automatic-research-panel" id="strategy-discovery-lab" data-automatic-research>
    <div className="panel-head automatic-research-head"><div>
      <span className="eyebrow">{t("autoResearch.eyebrow")}</span><h2>{t("autoResearch.title")}</h2>
      <p>{t("autoResearch.subtitle")}</p>
    </div><button className="text-button" onClick={()=>void load()}>{t("common.refresh")}</button></div>
    {message&&<p className="research-alert error" role="alert">{message}</p>}

    <div className="automatic-research-summary">
      <article><span>{t("autoResearch.latestCycle")}</span><div className="automatic-research-card-title">
        <b>{latest?`#${latest.id}`:"—"}</b>{latest&&<span className={`status-pill ${tone(latest.status)}`}>{value(latest.status)}</span>}
      </div><small>{latest?`${day(latest.research_start)} → ${day(latest.research_end)}`:t("autoResearch.noCycle")}</small></article>
      <article><span>{t("autoResearch.researchProgress")}</span>
        <b>{latest?(progressStage?value(progressStage.replace(/_complete$/," completed")):value(latest.status)):"—"}</b>
        <small>{t("autoResearch.candidatesEvaluated",{count:counts.development_candidates})}</small></article>
      <article><span>{t("autoResearch.activeStrategy")}</span><b>{active?.family||t("autoResearch.legacyBaseline")}</b>
        <small>{active?`${active.strategy_version} · ${active.direction_capability} · #${active.research_cycle_id}`:t("autoResearch.noApprovedCandidate")}</small></article>
      <article><span>{t("autoResearch.scheduler")}</span><div className="automatic-research-card-title">
        <b>{summary?.scheduler_enabled?t("autoResearch.enabled"):t("autoResearch.disabled")}</b>
        <span className={`status-pill ${summary?.scheduler_enabled?"healthy":"neutral"}`}>{interval?t("autoResearch.everyHours",{hours:interval}):"—"}</span>
      </div><small>{t("autoResearch.nextRun",{time:formatTime(summary?.next_due_at)})}</small></article>
    </div>

    <section className="automatic-research-details" aria-labelledby="latest-cycle-details">
      <div className="automatic-research-section-head"><div><span className="eyebrow">{t("autoResearch.evidence")}</span>
        <h3 id="latest-cycle-details">{t("autoResearch.latestCycleDetails")}</h3></div>
        {latest&&<span className={`status-pill ${tone(latest.status)}`}>{value(latest.status)}</span>}
      </div>
      <dl className="automatic-research-facts">
        <div><dt>{t("autoResearch.datasetRange")}</dt><dd>{latest?`${day(latest.research_start)} → ${day(latest.research_end)}`:"—"}</dd></div>
        <div><dt>{t("autoResearch.completedTime")}</dt><dd>{formatTime(latest?.completed_at)}</dd></div>
        <div><dt>{t("autoResearch.developmentCandidates")}</dt><dd>{counts.development_candidates}</dd></div>
        <div><dt>{t("autoResearch.eligibleFinalists")}</dt><dd>{counts.eligible_finalists}</dd></div>
        <div><dt>{t("autoResearch.approved")}</dt><dd>{latest?.result?.approved||0}</dd></div>
        <div><dt>{t("autoResearch.rejected")}</dt><dd>{counts.rejected_candidates}</dd></div>
      </dl>
      <div className="automatic-research-validation" aria-label={t("autoResearch.validationStatus")}>
        {validationStages.map(([label,status])=><div key={label}><span>{label}</span><b className={`validation-badge ${tone(status)}`}>{value(status)}</b></div>)}
      </div>
      {noEligibleCandidate&&<p className="automatic-research-notice"><strong>{t("autoResearch.noEligibilityTitle")}</strong><span>{t("autoResearch.noEligibilityHelp")}</span></p>}
      {latest?.dataset_fingerprint&&<details className="automatic-research-fingerprint"><summary>{t("autoResearch.datasetFingerprint")} · {compactHash(latest.dataset_fingerprint)}</summary><code>{latest.dataset_fingerprint}</code></details>}
    </section>

    <section className="automatic-research-recent" aria-labelledby="recent-research-cycles">
      <div className="automatic-research-section-head"><h3 id="recent-research-cycles">{t("autoResearch.recentCycles")}</h3></div>
      <div className="research-table-wrap"><table><thead><tr><th>{t("autoResearch.cycle")}</th><th>{t("autoResearch.range")}</th><th>{t("common.status")}</th><th>{t("autoResearch.candidates")}</th><th>{t("autoResearch.approved")}</th><th>{t("autoResearch.completed")}</th></tr></thead>
        <tbody>{summary?.recent_cycles?.length?summary.recent_cycles.map(cycle=><tr key={cycle.id} className={cycle.status==="FAILED"?"failed":""}>
          <td>#{cycle.id}</td><td>{day(cycle.research_start)} → {day(cycle.research_end)}</td>
          <td><span className={`status-pill ${tone(cycle.status)}`}>{value(cycle.status)}</span></td>
          <td>{cycle.research_counts?.development_candidates||0}</td><td>{cycle.result?.approved||0}</td><td>{formatTime(cycle.completed_at)}</td>
        </tr>):<tr><td colSpan={6}>{t("autoResearch.noCycle")}</td></tr>}</tbody></table></div>
    </section>
    <footer className="automatic-research-footnotes"><strong>{t("autoResearch.liveDisabled")}</strong><span>{t("autoResearch.scoreDisclaimer")}</span></footer>
  </section>;
}
