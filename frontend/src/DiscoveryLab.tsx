import { useEffect, useState } from "react";
import type { ApprovedStrategy } from "./data";

type Cycle = { id:number; status:string; research_start:number; research_end:number; created_at:string; started_at?:string; completed_at?:string; dataset_fingerprint?:string; checkpoint?:Record<string,unknown>; result?:{ approved?:number; rejected?:number; active_registry_id?:string } };
type Summary = { latest_cycle:Cycle|null; recent_cycles:Cycle[]; active_strategy:ApprovedStrategy|null; approved_strategies:ApprovedStrategy[] };
const date = (ts?:number) => ts ? new Date(ts*1000).toISOString().slice(0,10) : "--";

export default function DiscoveryLab() {
  const [summary,setSummary]=useState<Summary|null>(null); const [message,setMessage]=useState("");
  const load=()=>fetch("/api/automatic-research").then(async r=>{if(!r.ok)throw new Error(`HTTP ${r.status}`);return r.json()}).then(setSummary).catch(e=>setMessage(`Automatic Research unavailable: ${String(e)}`));
  useEffect(() => { void load(); },[]);
  const latest=summary?.latest_cycle, active=summary?.active_strategy;
  const progressKeys=Object.keys(latest?.checkpoint || {}), progressStage=progressKeys[progressKeys.length-1];
  return <section className="panel" id="strategy-discovery-lab" data-automatic-research>
    <div className="panel-head"><div><span className="eyebrow">PAPER / RESEARCH ONLY</span><h2>Automatic Research <small>自动策略研究</small></h2></div><button onClick={load}>Refresh</button></div>
    <p>Server-side durable workflow. Development ranking is frozen before Holdout, OOT and cross-asset evidence; AI does not select or activate strategies.</p>
    {message && <small role="alert">{message}</small>}
    <div className="metric-grid">
      <div><span>Latest cycle</span><b>{latest ? `#${latest.id} · ${latest.status}` : "No cycle yet"}</b><small>{latest ? `${date(latest.research_start)} → ${date(latest.research_end)} UTC` : "Scheduler is disabled by default"}</small></div>
      <div><span>Progress</span><b>{latest ? progressStage?.replace(/_/g," ") || latest.status : "--"}</b><small>{latest?.started_at || latest?.created_at || "--"}</small></div>
      <div><span>Latest active strategy</span><b>{active?.family || "Legacy Baseline"}</b><small>{active ? `${active.strategy_version} · ${active.direction_capability} · cycle #${active.research_cycle_id}` : "No approved registry candidate"}</small></div>
      <div><span>Validation</span><b>{active ? Object.values(active.serialized_definition.validation_status || {}).every(v=>v==="PASS") ? "ALL REQUIRED PASS" : "REVIEW" : "--"}</b><small>{active?.registry_id || "Registry empty"}</small></div>
    </div>
    <div className="table-wrap"><table><thead><tr><th>Cycle</th><th>Dataset range</th><th>Status</th><th>Approved / Rejected</th><th>Completed</th></tr></thead><tbody>{(summary?.recent_cycles || []).map(x=><tr key={x.id}><td>#{x.id}</td><td>{date(x.research_start)} → {date(x.research_end)}</td><td>{x.status}</td><td>{x.result ? `${x.result.approved || 0} / ${x.result.rejected || 0}` : "--"}</td><td>{x.completed_at || "--"}</td></tr>)}</tbody></table></div>
    <small>Scores are research ranking values, not win rates or profit probabilities. LIVE TRADING DISABLED.</small>
  </section>;
}
