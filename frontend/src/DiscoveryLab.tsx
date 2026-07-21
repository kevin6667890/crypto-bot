import { useEffect, useState } from "react";

type Dataset = { id:number; name:string; status:string; dataset_fingerprint?:string; start_ts:number; end_ts:number };
export default function DiscoveryLab() {
  const [items,setItems]=useState<Dataset[]>([]); const [message,setMessage]=useState("");
  const load=()=>fetch("/api/discovery/datasets").then(r=>r.json()).then(x=>setItems(x.items||[])).catch(()=>setMessage("Discovery API unavailable."));
  useEffect(() => { void load(); },[]);
  return <section className="panel" id="strategy-discovery-lab">
    <div className="panel-head"><div><span className="eyebrow">RESEARCH ONLY</span><h2>Strategy Discovery Lab <small>策略探索实验室</small></h2></div></div>
    <p>Discovery results use historical development data only. They do not authorize live trading or automatic strategy activation.</p>
    <p>探索结果仅基于历史开发数据，不构成实盘交易或自动启用策略的依据。</p>
    <button onClick={load}>Refresh datasets</button>{message && <small>{message}</small>}
    <div className="table-wrap"><table><thead><tr><th>Dataset</th><th>Fixed range</th><th>Status</th><th>Fingerprint</th></tr></thead><tbody>{items.map(x=><tr key={x.id}><td>{x.name}</td><td>2024-01-01 – 2026-01-01 (exclusive)</td><td>{x.status}</td><td>{x.dataset_fingerprint?.slice(0,16) || "Pending"}</td></tr>)}</tbody></table></div>
    <small>PRICE_ONLY is available by default. FLOW_OVERLAY remains disabled until real CVD/OI coverage is verified.</small>
  </section>;
}
