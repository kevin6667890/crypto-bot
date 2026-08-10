"""Summarize the required continuous AI-6B B0 production baseline."""
from __future__ import annotations
import argparse,base64,hashlib,json,math,statistics,subprocess
from datetime import datetime
from pathlib import Path

def percentile(values:list[float],q:float)->float:
 values=sorted(values);index=(len(values)-1)*q;low=math.floor(index);high=math.ceil(index)
 return values[low] if low==high else values[low]*(high-index)+values[high]*(index-low)
def main()->int:
 p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--output',required=True);p.add_argument('--host',required=True);p.add_argument('--identity-file',required=True);a=p.parse_args()
 records=[json.loads(line) for line in Path(a.input).read_text(encoding='utf-8').splitlines() if line.strip()];samples=[x['sample'] for x in records if x.get('ssh_exit_code')==0 and x.get('sample')]
 if len(samples)<2 or records[-1]['elapsed_seconds']<1800:raise SystemExit('BASELINE_WINDOW_INCOMPLETE')
 first,last=samples[0],samples[-1];start=first['timestamp'];elapsed=records[-1]['elapsed_seconds']
 remote="""import json,subprocess
names=['crypto-bot-frontend-1','crypto-bot-paper-api-1','crypto-bot-microstructure-collector-1'];out={}
for name in names:
 p=subprocess.run(['docker','logs','--since','START',name],text=True,capture_output=True,timeout=30);text=(p.stdout+'\\n'+p.stderr).lower();out[name]={'error':text.count('error'),'exception':text.count('exception'),'traceback':text.count('traceback'),'critical':text.count('critical'),'corrupt':text.count('corrupt')}
print(json.dumps(out,sort_keys=True))""".replace('START',start)
 encoded=base64.b64encode(remote.encode()).decode();cmd=['ssh','-i',a.identity_file,'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',a.host,f'echo {encoded} | base64 -d | python3 -'];error_counts=json.loads(subprocess.run(cmd,text=True,capture_output=True,timeout=60,check=True).stdout)
 http={path:{'status_codes':sorted({sample['http'][path]['status'] for sample in samples}),'p50_ms':round(percentile([sample['http'][path]['latency_ms'] for sample in samples],.5),3),'p95_ms':round(percentile([sample['http'][path]['latency_ms'] for sample in samples],.95),3),'max_ms':max(sample['http'][path]['latency_ms'] for sample in samples)} for path in first['http']}
 total_files=lambda s:sum(value or 0 for value in s['files'].values())
 observed=max(0,round((total_files(last)-total_files(first))/elapsed*86400))
 restarts={item['name']:{'start':next(x['restart_count'] for x in first['containers'] if x['name']==item['name']),'end':item['restart_count']} for item in last['containers']}
 all_ai_absent=all(all(value is None for name,value in sample['files'].items() if name.startswith('ai_market_reports.db')) for sample in samples)
 flags_off=all(all(value=='ABSENT_EFFECTIVE_FALSE' for value in sample['ai_flags'].values()) for sample in samples)
 aggregation={name:{'row_delta':last['aggregation'][name]['count']-first['aggregation'][name]['count'],'latest_start':first['aggregation'][name]['latest'],'latest_end':last['aggregation'][name]['latest']} for name in first['aggregation']}
 legacy={name:{'count_delta':last['legacy_ai']['briefs'][name]['count']-first['legacy_ai']['briefs'][name]['count'],'latest_start':first['legacy_ai']['briefs'][name]['latest'],'latest_end':last['legacy_ai']['briefs'][name]['latest']} for name in first['legacy_ai']['briefs']}
 swap_used_max=max(s['memory']['SwapTotal']-s['memory']['SwapFree'] for s in samples)
 swap_in_delta=last['vmstat'].get('pswpin',0)-first['vmstat'].get('pswpin',0);swap_out_delta=last['vmstat'].get('pswpout',0)-first['vmstat'].get('pswpout',0)
 oom_delta=last['vmstat'].get('oom_kill',0)-first['vmstat'].get('oom_kill',0);container_oom=any(item.get('oom_killed',False) for sample in samples for item in sample['containers'])
 healthy_all=all(all(item['health']=='healthy' for item in sample['containers']) for sample in samples)
 result={'gate':'AI6B-B0-30M-PRODUCTION-BASELINE','start':start,'end':last['timestamp'],'elapsed_seconds':elapsed,'sample_count':len(samples),'failed_probe_count':len(records)-len(samples),'production_mutations':0,'paper_orders':{'trade_count_delta':last['paper']['trade_count']-first['paper']['trade_count'],'max_trade_id_start':first['paper']['max_trade_id'],'max_trade_id_end':last['paper']['max_trade_id'],'open_start':first['paper']['open_trade_count'],'open_end':last['paper']['open_trade_count']},'router':{'decision_count_delta':last['paper']['router_decision_count']-first['paper']['router_decision_count']},'old_ai_brief':legacy,'collector_terminal_statuses':{name:value['status'] for name,value in last['collector'].items()},'aggregation':aggregation,'api':http,'resource':{'cpu_count':last['cpu_count'],'cloud_configured_memory_bytes':4*1024**3,'cloud_configuration_source':'PROJECT_OWNER_CONFIRMED','guest_memory_gate_min_bytes':int(3.25*1024**3),'memory_total_bytes':last['memory']['MemTotal'],'memory_available_last_bytes':last['memory']['MemAvailable'],'memory_available_min_bytes':min(s['memory']['MemAvailable'] for s in samples),'swap_total_bytes':last['memory']['SwapTotal'],'swap_used_last_bytes':last['memory']['SwapTotal']-last['memory']['SwapFree'],'swap_used_max_bytes':swap_used_max,'swap_in_delta_pages':swap_in_delta,'swap_out_delta_pages':swap_out_delta,'oom_kill_delta':oom_delta,'container_oom_killed':container_oom,'containers_healthy_all_samples':healthy_all,'disk_total_bytes':last['disk']['total'],'disk_free_min_bytes':min(s['disk']['free'] for s in samples),'disk_used_delta_bytes':last['disk']['used']-first['disk']['used'],'observed_non_ai_data_daily_growth_bytes':observed,'container_stats_last':last['container_stats']},'container_restarts':restarts,'error_token_counts_since_start':error_counts,'ai6b':{'database_absent_all_samples':all_ai_absent,'flags_off_all_samples':flags_off,'production_ai_writes':0 if all_ai_absent else 'UNKNOWN','production_migration_executed':False if all_ai_absent else 'UNKNOWN','real_live_provider_calls':0 if all_ai_absent and flags_off else 'UNKNOWN'},'status':'PASS' if len(records)==len(samples) and all_ai_absent and flags_off and healthy_all and not container_oom and oom_delta==0 and swap_in_delta==0 and swap_out_delta==0 and last['memory']['MemTotal']>=int(3.25*1024**3) and all(all(code==200 for code in item['status_codes']) for item in http.values()) and all(x['start']==x['end'] for x in restarts.values()) else 'FAIL'}
 output=Path(a.output).resolve();output.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n',encoding='utf-8');print(json.dumps({'output':str(output),'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'status':result['status']},sort_keys=True));return 0 if result['status']=='PASS' else 2
if __name__=='__main__':raise SystemExit(main())
