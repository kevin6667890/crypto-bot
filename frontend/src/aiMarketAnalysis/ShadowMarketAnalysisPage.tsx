import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, RefreshCw, ShieldCheck } from "lucide-react";
import { useLanguage } from "../i18n";
import { fetchPositionDetails, fetchPresentation, PresentationApiError } from "./api";
import { AuditPanel, Levels, Macro, OrderFlow, Scenarios, TimeframeMatrix, Timeline } from "./AnalysisPanels";
import { formatTime, safeEnum, shortId, warningPriority } from "./formatters";
import { shadowText } from "./i18n";
import { presentationKey } from "./queryKeys";
import { ReportHeader } from "./ReportHeader";
import { ReportSections } from "./ReportSections";
import { PresentationCache, RequestSequence } from "./state";
import { instruments, modes, type Instrument, type Presentation, type ReportMode } from "./types";

const cache = new PresentationCache<Presentation>();
const eligibilityCopy = (status: string, labels: Record<string,string>) => ({ AUDIT_PENDING:labels.pending, AUDIT_FAILED:labels.failed, AUDIT_ERROR:labels.auditError, AUDIT_NOT_FOUND:labels.notFound, AUDIT_SCHEMA_UPGRADE_REQUIRED:labels.upgrade }[status] || status);

export default function ShadowMarketAnalysisPage() {
  const { language } = useLanguage(); const labels = shadowText(language); const locale = language === "zh" ? "zh-CN" : "en";
  const reportLanguage = "zh-CN"; // Frozen audited body language; UI language never translates it.
  const [instrument,setInstrument]=useState<Instrument>("ETH-USDT-SWAP"); const [mode,setMode]=useState<ReportMode>("FULL");
  const [token,setToken]=useState(""); const [authorized,setAuthorized]=useState(false); const [authScope,setAuthScope]=useState(0);
  const [value,setValue]=useState<Presentation|null>(null); const [phase,setPhase]=useState<"idle"|"loading"|"error">("idle"); const [error,setError]=useState("");
  const [position,setPosition]=useState<Record<string,unknown>|null>(null); const sequence=useRef(new RequestSequence()); const lastManual=useRef(0);
  const key=useMemo(() => presentationKey({instrument,mode,language:reportLanguage,adminScope:`session-${authScope}`}),[instrument,mode,authScope]);
  const load=useCallback(async (manual=false) => {
    if (!authorized || !token) return; if (manual && Date.now()-lastManual.current<1000) return; if (manual) lastManual.current=Date.now();
    const request=sequence.current.begin(); setPhase("loading"); setError(""); setPosition(null);
    const cached=cache.get(key); if(cached) setValue(cached);
    try { const next=await fetchPresentation({instrument,mode,language:reportLanguage,token,signal:request.signal});
      if(!sequence.current.accepts(request.sequence) || next.instrument!==instrument || next.mode!==mode) return;
      cache.set(key,next); setValue(next); setPhase("idle");
    } catch(reason) { if((reason as Error).name==="AbortError") return; if(!sequence.current.accepts(request.sequence)) return;
      const code=reason instanceof PresentationApiError ? `${reason.status}:${reason.code}` : "CONTRACT_OR_NETWORK_ERROR"; setError(code); setValue(null); setPhase("error"); if(reason instanceof PresentationApiError && reason.status===401) setAuthorized(false);
    }
  },[authorized,token,key,instrument,mode]);
  useEffect(() => { if(!authorized)return; void load(); const timer=window.setInterval(()=>void load(),60_000); return()=>{window.clearInterval(timer);sequence.current.abort();}; },[authorized,load]);
  const authorize=(event:React.FormEvent)=>{event.preventDefault();cache.clear();setAuthScope(x=>x+1);setAuthorized(Boolean(token));};
  const loadPosition=async()=>{if(!value)return; const controller=new AbortController(); try{setPosition(await fetchPositionDetails({reportId:value.report_id,instrument,mode,token,signal:controller.signal}));}catch{setError("POSITION_DETAILS_UNAVAILABLE");}};
  return <main className="ama-shell">
    <a className="ama-back" href="/">← {labels.back}</a><header className="ama-appbar"><div><span>{labels.shadow}</span><h1>{labels.title}</h1></div><ShieldCheck aria-hidden="true" /></header>
    <section className="ama-controls" aria-label={labels.title}>
      <label>Instrument<select value={instrument} onChange={event=>setInstrument(event.target.value as Instrument)}>{instruments.map(item=><option key={item}>{item}</option>)}</select></label>
      <label>Mode<select value={mode} onChange={event=>setMode(event.target.value as ReportMode)}>{modes.map(item=><option key={item}>{item}</option>)}</select></label>
      {!authorized ? <form onSubmit={authorize}><label>{labels.token}<input type="password" autoComplete="off" value={token} onChange={event=>setToken(event.target.value)} /></label><button type="submit">{labels.authorize}</button></form> : <button onClick={()=>void load(true)} disabled={phase==="loading"}><RefreshCw size={16} aria-hidden="true" /> {labels.refresh}</button>}
    </section>
    {phase==="loading" && !value && <div className="ama-state" role="status" aria-live="polite">{labels.loading}</div>}
    {phase==="error" && <div className="ama-state ama-critical" role="alert"><AlertTriangle aria-hidden="true" /> {labels.error} · {error}</div>}
    {value && <>
      <section className="ama-latest" aria-label={labels.newest}><div><small>{labels.newest}</small><strong>{eligibilityCopy(value.latest_generated.eligibility,labels)}</strong><code>{shortId(value.latest_generated.report_id)}</code></div><div><small>{labels.displayed}</small><strong>{shortId(value.report_id)}</strong></div>{value.latest_generated.report_id!==value.report_id && <p role="status">{labels.staleOld}</p>}</section>
      {value.data_warnings.length>0 && <section className="ama-warnings" aria-labelledby="ama-warning-title" aria-live="polite"><h2 id="ama-warning-title"><AlertTriangle aria-hidden="true" /> {labels.warnings}</h2>{[...value.data_warnings].sort((a,b)=>warningPriority(a)-warningPriority(b)).map(w=><strong key={w}>{safeEnum(w)}</strong>)}</section>}
      {value.report ? <>
        <ReportHeader value={value} locale={locale} labels={labels}/><ReportSections value={value} labels={labels}/>
        <Timeline value={value} title={labels.timeline}/><TimeframeMatrix value={value} title={labels.matrix}/><OrderFlow value={value} title={labels.flow}/><Levels value={value} title={labels.levels}/><Scenarios value={value} title={labels.scenarios}/>
        <section className="ama-panel"><h2>{labels.position}</h2>{value.position_summary?.source && value.position_summary.source!=="NONE" ? <><p>{value.position_summary.source==="PAPER"?"Paper simulated position":value.position_summary.source==="USER_DECLARED"?"User-declared position":String(value.position_summary.source)}</p>{value.position_summary.sensitive_details_available && !position && <button onClick={()=>void loadPosition()}>{labels.sensitive}</button>}{position && <dl className="ama-ratios">{Object.entries(position).map(([k,v])=><div key={k}><dt>{safeEnum(k)}</dt><dd>{String(v)}</dd></div>)}</dl>}</> : <p>{labels.noPosition}</p>}</section>
        <Macro value={value} title={labels.macro} empty={labels.noMacro}/>{value.audit_summary&&<AuditPanel value={value} title={labels.audit}/>} 
        <details className="ama-panel"><summary>{labels.provenance}</summary><dl className="ama-ratios"><div><dt>Report version</dt><dd>{value.report_schema_version}</dd></div><div><dt>Prompt version</dt><dd>{value.report.prompt_version}</dd></div><div><dt>Provider / model</dt><dd>{value.report.model}</dd></div><div><dt>Context</dt><dd>{shortId(value.context_id)}</dd></div><div><dt>Registry</dt><dd>{shortId(value.registry_snapshot_id)}</dd></div><div><dt>Audit version</dt><dd>{value.audit_schema_version}</dd></div><div><dt>Presentation hash</dt><dd>{shortId(value.presentation_hash)}</dd></div></dl></details>
        <details className="ama-panel"><summary>{labels.health}</summary><pre>{JSON.stringify(value.health_summary,null,2)}</pre></details>
      </> : <section className="ama-state ama-eligibility" role="status"><h2>{eligibilityCopy(value.eligibility,labels)}</h2><p>Report {shortId(value.report_id)} · Request {shortId(value.request_id)}</p>{value.audit_summary?.hard_failure_count ? <p className="ama-critical">Hard failures: {value.audit_summary.hard_failure_count} · {value.audit_summary.hard_failures.join(", ")}</p>:null}</section>}
    </>}
    <footer>AI-6A Shadow candidate · {value?.decision_time&&formatTime(value.decision_time,locale)} · Never production ready</footer>
  </main>;
}
