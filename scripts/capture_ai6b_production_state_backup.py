"""Capture a sanitized, read-only production state backup over SSH."""
from __future__ import annotations
import argparse,base64,hashlib,json,os,subprocess
from datetime import datetime,timezone
from pathlib import Path

REMOTE=r'''
import hashlib,json,os,re,subprocess
ROOT='/opt/crypto-bot'
def run(args):
 p=subprocess.run(args,text=True,capture_output=True,timeout=30);return p.returncode,p.stdout.strip()
def sensitive(key):return any(x in key.lower() for x in ('secret','token','password','authorization','api_key','apikey','webhook','credential'))
code,ids=run(['docker','compose','-f',ROOT+'/docker-compose.yml','ps','-q']);items=[];config_paths=set()
for cid in ids.splitlines() if code==0 else []:
 code,value=run(['docker','inspect','--format','{{json .}}',cid])
 if code:continue
 item=json.loads(value);labels=item.get('Config',{}).get('Labels') or {};path=labels.get('com.docker.compose.project.config_files');
 if path:config_paths.add(path)
 safe_env={}
 allowed=('AI_MARKET_REPORTS_ENABLED','AI_MARKET_REPORT_WORKER_ENABLED','AI_USER_POSITION_PLANS_ENABLED','AI_MACRO_HTTP_FETCH_ENABLED','AI_REPORT_LIVE_PROVIDER_ENABLED','AI_REPORT_AUDIT_ENABLED','AI_REPORT_AUDIT_WORKER_ENABLED','AI_REPORT_AUTO_AUDIT_ENABLED','AI_REPORT_EVALUATION_ENABLED','AI_MARKET_ANALYSIS_PRESENTATION_ENABLED')
 for raw in item.get('Config',{}).get('Env') or []:
  key,_,value=raw.partition('=')
  if key in allowed:safe_env[key]=value
 items.append({'name':item['Name'].lstrip('/'),'image_id':item['Image'],'image_ref':item['Config']['Image'],'restart_count':item['RestartCount'],'command':item['Config'].get('Cmd'),'entrypoint':item['Config'].get('Entrypoint'),'user':item['Config'].get('User'),'working_dir':item['Config'].get('WorkingDir'),'mounts':[{'destination':m['Destination'],'source':m['Source'],'mode':m.get('Mode'),'rw':m['RW']} for m in item.get('Mounts',[])],'healthcheck':item['Config'].get('Healthcheck'),'restart_policy':item['HostConfig'].get('RestartPolicy'),'resources':{'memory':item['HostConfig'].get('Memory'),'nano_cpus':item['HostConfig'].get('NanoCpus'),'pids_limit':item['HostConfig'].get('PidsLimit')},'safe_ai_flags':safe_env,'compose_config_files':path,'compose_working_dir':labels.get('com.docker.compose.project.working_dir')})
images=[]
for image_id in sorted({x['image_id'] for x in items}):
 code,value=run(['docker','image','inspect','--format','{{json .}}',image_id])
 if code==0:
  image=json.loads(value);images.append({'id':image['Id'],'repo_tags':image.get('RepoTags') or [],'repo_digests':image.get('RepoDigests') or [],'created':image.get('Created'),'architecture':image.get('Architecture'),'os':image.get('Os')})
manifests=[]
for path in sorted(config_paths):
 raw=open(path,encoding='utf-8').read();san=[]
 for line in raw.splitlines():
  match=re.match(r'^(\s*)([^:#]+):\s*(.*)$',line)
  san.append((match.group(1)+match.group(2)+': <REDACTED>') if match and sensitive(match.group(2)) else line)
 manifests.append({'path':path,'sha256':hashlib.sha256(raw.encode()).hexdigest(),'sanitized_content':'\n'.join(san)+'\n','sensitive_values_copied':False})
head=run(['git','-C',ROOT,'rev-parse','HEAD'])[1];dirty=run(['git','-C',ROOT,'status','--porcelain=v1'])[1].splitlines()
print(json.dumps({'captured_at':__import__('time').strftime('%Y-%m-%dT%H:%M:%SZ',__import__('time').gmtime()),'deployment_head':head,'deployment_dirty_paths':dirty,'containers':items,'images':images,'deployment_manifests':manifests,'ai_report_database_status':'DATABASE_NOT_YET_PRESENT' if not os.path.isfile(ROOT+'/data_cache/ai_market_reports.db') else 'PRESENT','secrets_copied':False},sort_keys=True))
'''

def write(path:Path,value:object)->None:path.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n',encoding='utf-8')
def sha(path:Path)->str:return hashlib.sha256(path.read_bytes()).hexdigest()
def main()->int:
 parser=argparse.ArgumentParser();parser.add_argument('--host',required=True);parser.add_argument('--identity-file',required=True);parser.add_argument('--output-directory',required=True);args=parser.parse_args()
 output=Path(args.output_directory).resolve();output.mkdir(parents=True,exist_ok=False);encoded=base64.b64encode(REMOTE.encode()).decode()
 command=['ssh','-i',args.identity_file,'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',args.host,f'echo {encoded} | base64 -d | python3 -'];completed=subprocess.run(command,text=True,capture_output=True,timeout=60,check=True);state=json.loads(completed.stdout)
 files={
  'deployment-state.json':{'captured_at':state['captured_at'],'deployment_head':state['deployment_head'],'deployment_dirty_paths':state['deployment_dirty_paths'],'ai_report_database_status':state['ai_report_database_status']},
  'running-images.json':{'captured_at':state['captured_at'],'images':state['images']},
  'sanitized-compose.json':{'captured_at':state['captured_at'],'deployment_manifests':state['deployment_manifests'],'secrets_copied':False},
  'ai-flag-snapshot.json':{'captured_at':state['captured_at'],'containers':{x['name']:x['safe_ai_flags'] for x in state['containers']},'absent_means_effective_false_for_ai6b':True},
  'legacy-critical-config.json':{'captured_at':state['captured_at'],'containers':state['containers'],'secret_values_copied':False},
 }
 for name,value in files.items():write(output/name,value)
 manifest={'backup_id':'ai6b-production-state-'+state['captured_at'].replace(':','').replace('-',''),'created_at':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'location_class':'OFF_PRODUCTION_UNIQUE_DATA_DIRECTORY','database_status':state['ai_report_database_status'],'database_backup_created':False,'artifacts':[{'file':name,'size_bytes':(output/name).stat().st_size,'sha256':sha(output/name)} for name in sorted(files)],'secrets_copied':False,'raw_provider_responses_copied':False,'prompts_copied':False}
 write(output/'backup-manifest.json',manifest);print(json.dumps({'output':str(output),'manifest_sha256':sha(output/'backup-manifest.json'),'database_status':state['ai_report_database_status']},sort_keys=True));return 0
if __name__=='__main__':raise SystemExit(main())
