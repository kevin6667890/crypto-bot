import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, RefreshCw, ShieldCheck } from "lucide-react";
import { useLanguage } from "../i18n";
import { fetchPositionDetails, fetchPresentation, PresentationApiError } from "./api";
import { AuditPanel, Levels, Macro, OrderFlow, Scenarios, TimeframeMatrix, Timeline } from "./AnalysisPanels";
import { formatTime, shortId, warningPriority } from "./formatters";
import { shadowText } from "./i18n";
import { presentationKey } from "./queryKeys";
import { ReportHeader } from "./ReportHeader";
import { ReportSections } from "./ReportSections";
import { PresentationCache, RequestSequence } from "./state";
import { instruments, modes, type Instrument, type Presentation, type ReportMode } from "./types";
import { translateEnum, type UiLanguage } from "./enumTranslations";
import { WarningItem } from "./WarningItem";
import { ErrorCode } from "./ErrorCode";
import { PositionPanel } from "./PositionPanel";
import { ProvenancePanel } from "./ProvenancePanel";
import { HealthPanel } from "./HealthPanel";

export const presentationCache = new PresentationCache<Presentation>();

export default function ShadowMarketAnalysisPage(){
  const {language}=useLanguage();const uiLanguage=language as UiLanguage;const labels=shadowText(language);const locale=language==="zh"?"zh-CN":"en";
  const reportLanguage="zh-CN"; // The audited report body remains frozen; UI language never translates it.
  const [instrument,setInstrument]=useState<Instrument>("ETH-USDT-SWAP"),[mode,setMode]=useState<ReportMode>("FULL");
  const [token,setToken]=useState(""),[authorized,setAuthorized]=useState(false),[authScope,setAuthScope]=useState(0);
  const [value,setValue]=useState<Presentation|null>(null),[phase,setPhase]=useState<"idle"|"loading"|"error">("idle"),[error,setError]=useState("");
  const [position,setPosition]=useState<Record<string,unknown>|null>(null);const sequence=useRef(new RequestSequence());const lastManual=useRef(0);
  const key=useMemo(()=>presentationKey({instrument,mode,language:reportLanguage,adminScope:`session-${authScope}`}),[instrument,mode,authScope]);
  const load=useCallback(async(reason:"cache-first"|"refresh"="cache-first")=>{
    if(!authorized||!token)return;if(reason==="refresh"&&Date.now()-lastManual.current<1000)return;if(reason==="refresh")lastManual.current=Date.now();
    const cached=presentationCache.get(key);if(cached&&reason==="cache-first"){setValue(cached);setPhase("idle");return;}
    const request=sequence.current.begin();setPhase("loading");setError("");setPosition(null);if(cached)setValue(cached);
    try{const next=await fetchPresentation({instrument,mode,language:reportLanguage,token,signal:request.signal});if(!sequence.current.accepts(request.sequence)||next.instrument!==instrument||next.mode!==mode)return;presentationCache.set(key,next);setValue(next);setPhase("idle");}
    catch(reasonValue){if((reasonValue as Error).name==="AbortError")return;if(!sequence.current.accepts(request.sequence))return;const code=reasonValue instanceof PresentationApiError?`${reasonValue.status}:${reasonValue.code}`:"CONTRACT_OR_NETWORK_ERROR";setError(code);setValue(null);setPhase("error");if(reasonValue instanceof PresentationApiError&&reasonValue.status===401)setAuthorized(false);}
  },[authorized,token,key,instrument,mode]);
  useEffect(()=>{if(!authorized)return;void load("cache-first");const timer=window.setInterval(()=>void load("refresh"),60_000);return()=>{window.clearInterval(timer);sequence.current.abort();};},[authorized,load]);
  const authorize=(event:React.FormEvent)=>{event.preventDefault();presentationCache.clear();setAuthScope(x=>x+1);setAuthorized(Boolean(token));};
  const loadPosition=async()=>{if(!value)return;const controller=new AbortController();try{setPosition(await fetchPositionDetails({reportId:value.report_id,instrument,mode,token,signal:controller.signal}));}catch{setError("POSITION_DETAILS_UNAVAILABLE");}};
  return <main className="ama-shell"><a className="ama-back" href="/">← {labels.back}</a><header className="ama-appbar"><div><span>{labels.shadow}</span><h1>{labels.title}</h1></div><ShieldCheck aria-hidden="true"/></header>
    <section className="ama-controls" aria-label={labels.title}><label>{labels.instrument}<select value={instrument} onChange={event=>setInstrument(event.target.value as Instrument)}>{instruments.map(item=><option key={item} value={item}>{item}</option>)}</select></label><label>{labels.mode}<select value={mode} onChange={event=>setMode(event.target.value as ReportMode)}>{modes.map(item=><option key={item} value={item}>{translateEnum("report_mode",item,uiLanguage)}</option>)}</select></label>{!authorized?<form onSubmit={authorize}><label>{labels.token}<input type="password" autoComplete="off" value={token} onChange={event=>setToken(event.target.value)}/></label><button type="submit">{labels.authorize}</button></form>:<button onClick={()=>void load("refresh")} disabled={phase==="loading"}><RefreshCw size={16} aria-hidden="true"/> {labels.refresh}</button>}</section>
    {phase==="loading"&&!value&&<div className="ama-state" role="status" aria-live="polite">{labels.loading}</div>}{phase==="error"&&<div className="ama-state ama-critical" role="alert"><AlertTriangle aria-hidden="true"/> {labels.error} · <ErrorCode code={error} language={uiLanguage}/></div>}
    {value&&<><section className="ama-latest" aria-label={labels.newest}><div><small>{labels.newest}</small><strong>{translateEnum("eligibility",value.latest_generated.eligibility,uiLanguage)}</strong><code>{shortId(value.latest_generated.report_id)}</code></div><div><small>{labels.displayed}</small><strong>{shortId(value.report_id)}</strong></div>{value.latest_generated.report_id!==value.report_id&&<p role="status">{labels.staleOld}</p>}</section>
      {value.data_warnings.length>0&&<section className="ama-warnings" aria-labelledby="ama-warning-title" aria-live="polite"><h2 id="ama-warning-title"><AlertTriangle aria-hidden="true"/> {labels.warnings}</h2>{[...value.data_warnings].sort((a,b)=>warningPriority(a)-warningPriority(b)).map(w=><WarningItem key={w} code={w} language={uiLanguage}/>)}</section>}
      {value.report?<><ReportHeader value={value} locale={locale} labels={labels} language={uiLanguage}/><ReportSections value={value} labels={labels} language={uiLanguage}/><Timeline value={value} labels={labels} language={uiLanguage}/><TimeframeMatrix value={value} labels={labels} language={uiLanguage}/><OrderFlow value={value} labels={labels} language={uiLanguage}/><Levels value={value} labels={labels} language={uiLanguage}/><Scenarios value={value} labels={labels} language={uiLanguage}/><PositionPanel summary={value.position_summary} details={position} onLoad={()=>void loadPosition()} labels={labels} language={uiLanguage}/><Macro value={value} labels={labels} language={uiLanguage}/>{value.audit_summary&&<AuditPanel value={value} labels={labels} language={uiLanguage}/>}<ProvenancePanel value={value} labels={labels} language={uiLanguage}/><HealthPanel health={value.health_summary} labels={labels} language={uiLanguage}/></>:<section className="ama-state ama-eligibility" role="status"><h2>{translateEnum("eligibility",value.eligibility,uiLanguage)}</h2><p>{labels.report} {shortId(value.report_id)} · {labels.request} {shortId(value.request_id)}</p>{value.audit_summary?.hard_failure_count?<p className="ama-critical">{labels.hardFailures}: {value.audit_summary.hard_failure_count}</p>:null}</section>}</>}
    <footer>{labels.shadowCandidate} · {value?.decision_time&&formatTime(value.decision_time,locale)} · {labels.neverProduction}</footer></main>;
}
