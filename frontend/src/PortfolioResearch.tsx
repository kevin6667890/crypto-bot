import { useCallback, useEffect, useState } from "react";
import { Play } from "lucide-react";
import { researchApi, type ResearchJob, type StrategyParameters } from "./research";
import { useLanguage } from "./i18n";

const ACTIVE = new Set(["QUEUED", "RUNNING", "CANCEL_REQUESTED"]);

export default function PortfolioResearch({startDate,endDate,parameters}:{startDate:string;endDate:string;parameters:StrategyParameters}){
  const {t,message}=useLanguage();
  const [assets,setAssets]=useState(["BTC-USDT","ETH-USDT","SOL-USDT"]);
  const [job,setJob]=useState<ResearchJob|null>(null);
  const [result,setResult]=useState<Record<string,any>|null>(null);
  const [error,setError]=useState("");

  const loadResult=useCallback(async(next:ResearchJob)=>{
    if(next.status!=="COMPLETED"||!next.result_ref)return;
    const ref=JSON.parse(next.result_ref);
    setResult(await researchApi.portfolioRun(ref.portfolio_run_id));
  },[]);

  useEffect(()=>{
    let disposed=false;
    researchApi.jobs().then(async jobs=>{
      const active=jobs.find(item=>item.job_type==="PORTFOLIO_BACKTEST"&&ACTIVE.has(item.status));
      const completed=jobs.find(item=>item.job_type==="PORTFOLIO_BACKTEST"&&item.status==="COMPLETED"&&item.result_ref);
      const recovered=active||completed;
      if(!disposed&&recovered){setJob(recovered);if(!active)await loadResult(recovered);}
    }).catch(()=>undefined);
    return()=>{disposed=true};
  },[loadResult]);

  useEffect(()=>{
    if(!job||!ACTIVE.has(job.status))return;
    let disposed=false;
    const poll=async()=>{
      try{
        const next=await researchApi.job(job.id);
        if(disposed)return;
        setJob(next);
        if(next.status==="COMPLETED")await loadResult(next);
        if(next.status==="FAILED"||next.status==="INTERRUPTED")setError(next.error||message(next.message_code,next.message_params,next.progress_message));
        if(next.status==="CANCELLED")setError("");
      }catch(e){if(!disposed)setError(e instanceof Error?e.message:t("common.error"));}
    };
    poll(); const timer=window.setInterval(poll,1000);
    return()=>{disposed=true;window.clearInterval(timer)};
  },[job?.id,job?.status,loadResult,message,t]);

  async function run(){
    setError("");setResult(null);
    try{
      const next=await researchApi.portfolio({assets,start_date:startDate,end_date:endDate,parameters,initial_capital:parameters.initial_capital,max_positions:3,max_asset_weight:.5,max_asset_risk:.015,max_portfolio_risk:.03,max_long_exposure:1,max_short_exposure:1,asset_weights:Object.fromEntries(assets.map(a=>[a,1/assets.length]))});
      setJob(next);
      if(next.status==="COMPLETED")await loadResult(next);
    }catch(e){setError(e instanceof Error?e.message:t("portfolio.couldNotStart"));}
  }
  const metrics=result?.result?.metrics;
  const progressText=job?message(job.message_code,job.message_params,job.progress_message):"";
  const cards:Array<[ReturnType<typeof t>,number|undefined,string]>=[
    [t("portfolio.return"),metrics?.total_return,"%"],[t("portfolio.maxDrawdown"),metrics?.maximum_drawdown,"%"],
    [t("portfolio.sharpe"),metrics?.sharpe_ratio,""],[t("portfolio.totalTrades"),metrics?.total_trades,""],
    [t("portfolio.exposure"),metrics?.exposure,"%"],[t("portfolio.cashUtilization"),metrics?.cash_utilization,"%"],
    [t("portfolio.longExposure"),metrics?.long_exposure,"%"],[t("portfolio.shortExposure"),metrics?.short_exposure,"%"],
    [t("portfolio.concurrentPositions"),metrics?.concurrent_positions,""]
  ];
  return <section className="research-panel">
    <div className="research-panel-head"><div><span className="eyebrow">{t("portfolio.stream")}</span><h2>{t("portfolio.backtest")}</h2></div><button className="primary-btn" onClick={run} disabled={!assets.length||!!job&&ACTIVE.has(job.status)}><Play size={14}/>{t("portfolio.run")}</button></div>
    <div className="portfolio-assets">{["BTC-USDT","ETH-USDT","SOL-USDT"].map(a=><label key={a}><input type="checkbox" checked={assets.includes(a)} onChange={e=>setAssets(v=>e.target.checked?[...v,a]:v.filter(x=>x!==a))}/>{a}</label>)}</div>
    {job&&<div className={`research-progress ${job.status.toLowerCase()}`}><div><span>{progressText}{job.queue_position?` · ${t("portfolio.queuePosition",{position:job.queue_position})}`:""}</span><b>{job.progress}%</b></div><i><span style={{width:`${job.progress}%`}}/></i></div>}
    {error&&<div className="research-alert error">{error}</div>}
    {metrics&&<><div className="research-metrics">{cards.map(([label,value,suffix])=><article key={label}><span>{label}</span><strong>{typeof value==="number"?`${value.toFixed(2)}${suffix}`:t("common.insufficientData")}</strong></article>)}</div><div className="research-table-wrap"><table><thead><tr><th>{t("common.asset")}</th><th>{t("portfolio.pnl")}</th><th>{t("portfolio.contribution")}</th><th>{t("common.fees")}</th></tr></thead><tbody>{assets.map(a=><tr key={a}><td>{a}</td><td>{metrics.per_asset_pnl?.[a]?.toFixed(2)??"0.00"}</td><td>{metrics.per_asset_contribution?.[a]?.toFixed(2)??"0.00"}%</td><td>{metrics.fees_by_asset?.[a]?.toFixed(2)??"0.00"}</td></tr>)}</tbody></table></div></>}
  </section>;
}
