"""Collect a bounded, read-only production baseline over SSH."""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

REMOTE_PROBE = r'''
import json,os,sqlite3,subprocess,time,urllib.request,shutil
ROOT='/opt/crypto-bot';DATA=ROOT+'/data_cache'
def run(args):
 p=subprocess.run(args,text=True,capture_output=True,timeout=20);return p.returncode,p.stdout.strip()
def scalar(c,q,args=()): return c.execute(q,args).fetchone()[0]
out={'timestamp':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
code,head=run(['git','-C',ROOT,'rev-parse','HEAD']);out['deployment_head']=head if code==0 else 'UNKNOWN'
code,status=run(['git','-C',ROOT,'status','--porcelain=v1']);out['deployment_dirty_paths']=status.splitlines() if code==0 else ['UNKNOWN']
disk=shutil.disk_usage('/');out['disk']={'total':disk.total,'used':disk.used,'free':disk.free}
with open('/proc/meminfo') as f:
 values={line.split(':')[0]:int(line.split()[1])*1024 for line in f if line.startswith(('MemTotal:','MemAvailable:','SwapTotal:','SwapFree:'))}
with open('/proc/vmstat') as f:
 vmstat={line.split()[0]:int(line.split()[1]) for line in f if line.startswith(('pswpin ','pswpout ','oom_kill '))}
out['memory']=values;out['vmstat']=vmstat;out['cpu_count']=os.cpu_count()
out['files']={}
for name in ('paper_trades.db','paper_trades.db-wal','market_microstructure.db','market_microstructure.db-wal','canonical_microstructure_history_v1.db','canonical_microstructure_history_v1.db-wal','ai_market_reports.db','ai_market_reports.db-wal','ai_market_reports.db-shm'):
 p=DATA+'/'+name;out['files'][name]=os.path.getsize(p) if os.path.isfile(p) else None
p=DATA+'/paper_trades.db';c=sqlite3.connect('file:'+p+'?mode=ro',uri=True,timeout=5);c.row_factory=sqlite3.Row
out['paper']={'trade_count':scalar(c,'select count(*) from paper_trades'),'open_trade_count':scalar(c,"select count(*) from paper_trades where status='OPEN'"),'max_trade_id':scalar(c,'select coalesce(max(id),0) from paper_trades'),'router_decision_count':scalar(c,'select count(*) from decision_evaluations')}
out['legacy_ai']={'brief_total':scalar(c,'select count(*) from ai_briefs'),'briefs':{}}
for row in c.execute("select coalesce(instrument,'UNKNOWN') instrument,count(*) count,max(created_at) latest from ai_briefs group by coalesce(instrument,'UNKNOWN')"):
 out['legacy_ai']['briefs'][row['instrument']]={'count':row['count'],'latest':row['latest']}
out['legacy_ai']['health']={row['instrument']:{'last_success_at':row['last_success_at'],'last_attempt_at':row['last_attempt_at'],'failure_count':row['failure_count'],'next_retry_at':row['next_retry_at'],'updated_at':row['updated_at']} for row in c.execute('select * from ai_health')};c.close()
p=DATA+'/market_microstructure.db';c=sqlite3.connect('file:'+p+'?mode=ro',uri=True,timeout=5);c.row_factory=sqlite3.Row
out['aggregation']={name:{'count':scalar(c,'select count(*) from '+name),'latest':scalar(c,'select max(bucket_ms) from '+name)} for name in ('cvd_aggregates','oi_aggregates','basis_aggregates')}
out['collector']={row['component']:{'status':row['status'],'last_success_ms':row['last_success_ms'],'reconnect_count':row['reconnect_count'],'failed_request_count':row['failed_request_count'],'retry_count':row['retry_count'],'source_lag_ms':row['source_lag_ms'],'updated_at_ms':row['updated_at_ms']} for row in c.execute('select * from collector_health')};c.close()
out['http']={}
for path in ('/','/api/health','/api/paper/flow/health'):
 started=time.monotonic()
 try:
  with urllib.request.urlopen('http://127.0.0.1:8501'+path,timeout=10) as response: status=response.status;response.read()
 except Exception as error: status=getattr(error,'code',0)
 out['http'][path]={'status':status,'latency_ms':round((time.monotonic()-started)*1000,3)}
flags=('AI_MARKET_REPORTS_ENABLED','AI_MARKET_REPORT_WORKER_ENABLED','AI_USER_POSITION_PLANS_ENABLED','AI_MACRO_HTTP_FETCH_ENABLED','AI_REPORT_LIVE_PROVIDER_ENABLED','AI_REPORT_AUDIT_ENABLED','AI_REPORT_AUDIT_WORKER_ENABLED','AI_REPORT_AUTO_AUDIT_ENABLED','AI_REPORT_EVALUATION_ENABLED','AI_MARKET_ANALYSIS_PRESENTATION_ENABLED','VITE_AI_MARKET_ANALYSIS_SHADOW_ENABLED')
code,ids=run(['docker','compose','-f',ROOT+'/docker-compose.yml','ps','-q']);containers=[]
for cid in ids.splitlines() if code==0 else []:
 code,value=run(['docker','inspect','--format','{{json .}}',cid])
 if code==0:
  item=json.loads(value);health=(item.get('State',{}).get('Health') or {}).get('Status','none')
  environment=dict(raw.partition('=')[::2] for raw in item.get('Config',{}).get('Env') or [])
  containers.append({'name':item['Name'].lstrip('/'),'image_id':item['Image'],'image_ref':item['Config']['Image'],'restart_count':item['RestartCount'],'health':health,'started_at':item['State']['StartedAt'],'oom_killed':bool(item.get('State',{}).get('OOMKilled',False)),'memory_limit_bytes':item.get('HostConfig',{}).get('Memory',0),'safe_ai_flags':{flag:environment[flag] for flag in flags if flag in environment}})
out['containers']=containers
code,stats=run(['docker','stats','--no-stream','--format','{{json .}}']);out['container_stats']=[json.loads(line) for line in stats.splitlines()] if code==0 else []
out['ai_flags']={}
for flag in flags:
 explicit=[container['safe_ai_flags'][flag].strip().lower() for container in containers if flag in container['safe_ai_flags']]
 out['ai_flags'][flag]='ABSENT_EFFECTIVE_FALSE' if not explicit else ('EXPLICIT_FALSE' if all(value=='false' for value in explicit) else 'UNEXPECTED:'+','.join(sorted(set(explicit))))
print(json.dumps(out,sort_keys=True,separators=(',',':')))
'''


def main() -> int:
    parser=argparse.ArgumentParser();parser.add_argument('--host',required=True);parser.add_argument('--identity-file',required=True);parser.add_argument('--output',required=True);parser.add_argument('--duration-seconds',type=int,default=1800);parser.add_argument('--interval-seconds',type=int,default=60);args=parser.parse_args()
    if args.duration_seconds < 1800:raise SystemExit('duration must be at least 1800 seconds')
    output=Path(args.output).resolve();output.parent.mkdir(parents=True,exist_ok=True)
    encoded=base64.b64encode(REMOTE_PROBE.encode()).decode();started=time.monotonic();samples=0
    with output.open('x',encoding='utf-8') as stream:
        while True:
            command=['ssh','-i',args.identity_file,'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10',args.host,f'echo {encoded} | base64 -d | python3 -']
            completed=subprocess.run(command,text=True,capture_output=True,timeout=45)
            record={'collected_at':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'elapsed_seconds':round(time.monotonic()-started,3),'ssh_exit_code':completed.returncode}
            if completed.returncode==0:
                record['sample']=json.loads(completed.stdout)
            else:
                record['probe_error']='SSH_OR_PROBE_FAILED'
            stream.write(json.dumps(record,sort_keys=True,separators=(',',':'))+'\n');stream.flush();samples+=1
            elapsed=time.monotonic()-started
            if elapsed>=args.duration_seconds:break
            time.sleep(min(args.interval_seconds,max(0,args.duration_seconds-elapsed)))
    print(json.dumps({'output':str(output),'samples':samples,'duration_seconds':round(time.monotonic()-started,3)},sort_keys=True));return 0
if __name__=='__main__':raise SystemExit(main())
