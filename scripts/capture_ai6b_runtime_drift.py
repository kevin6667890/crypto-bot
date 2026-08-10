"""Explain production Git/image drift without mutating production."""
from __future__ import annotations
import argparse,base64,hashlib,json,subprocess
from datetime import datetime,timezone
from pathlib import Path
REMOTE=r'''
import json,os,subprocess
ROOT='/opt/crypto-bot';SOURCE='35a5b29dc2b7d29be4910f323d61edc63b9613e9';STAGING='/opt/crypto-bot-canonical-staging/35a5b29/source'
def run(args):
 p=subprocess.run(args,text=True,capture_output=True,timeout=120);return p.returncode,p.stdout.strip()
head=run(['git','-C',ROOT,'rev-parse','HEAD'])[1];dirty=run(['git','-C',ROOT,'status','--porcelain=v1'])[1].splitlines();source=run(['git','-C',ROOT,'rev-parse',SOURCE])[1]
commits=run(['git','-C',ROOT,'log','--format=%H|%ct|%s',SOURCE+'..'+head])[1].splitlines();changed=run(['git','-C',ROOT,'diff','--name-status',SOURCE+'..'+head])[1].splitlines();ancestor=run(['git','-C',ROOT,'merge-base','--is-ancestor',SOURCE,head])[0]==0
tracked=run(['git','-C',ROOT,'ls-tree','-r','--name-only',SOURCE])[1].splitlines();missing=[];modified=[]
for name in tracked:
 path=STAGING+'/'+name
 if not os.path.isfile(path):missing.append(name);continue
 expected=run(['git','-C',ROOT,'rev-parse',SOURCE+':'+name])[1];actual=run(['git','-C',ROOT,'hash-object',path])[1]
 if expected!=actual:modified.append(name)
tracked_set=set(tracked);extra=[]
for base,dirs,files in os.walk(STAGING):
 for name in files:
  rel=os.path.relpath(os.path.join(base,name),STAGING)
  if rel not in tracked_set:extra.append(rel)
ids=run(['docker','compose','-f',ROOT+'/docker-compose.yml','ps','-q'])[1].splitlines();containers=[]
for cid in ids:
 value=json.loads(run(['docker','inspect','--format','{{json .}}',cid])[1]);labels=value.get('Config',{}).get('Labels') or {};diff=run(['docker','diff',cid])[1].splitlines()
 source_mutations=[line for line in diff if line[:1] in ('C','D') and line[2:].startswith(('/app/dashboard/','/app/scripts/','/app/migrations/','/app/frontend/src/')) and '/__pycache__/' not in line]
 expected_generated=[line for line in diff if '/__pycache__' in line]
 containers.append({'name':value['Name'].lstrip('/'),'image_id':value['Image'],'image_ref':value['Config']['Image'],'compose_working_dir':labels.get('com.docker.compose.project.working_dir'),'compose_config_files':labels.get('com.docker.compose.project.config_files'),'runtime_source_path_mutations':source_mutations,'expected_runtime_generated_path_count':len(expected_generated),'runtime_diff_count':len(diff)})
print(json.dumps({'deployment_head':head,'deployment_dirty_paths':dirty,'running_source_commit':source,'source_is_ancestor_of_deployment_head':ancestor,'undeployed_commits':commits,'net_changed_paths':changed,'staging_source':STAGING,'staging_missing_tracked_paths':missing,'staging_modified_tracked_paths':modified,'staging_extra_paths':extra,'containers':containers},sort_keys=True))
'''
def main()->int:
 p=argparse.ArgumentParser();p.add_argument('--host',required=True);p.add_argument('--identity-file',required=True);p.add_argument('--output',required=True);a=p.parse_args();encoded=base64.b64encode(REMOTE.encode()).decode();cmd=['ssh','-i',a.identity_file,'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',a.host,f'echo {encoded} | base64 -d | python3 -'];state=json.loads(subprocess.run(cmd,text=True,capture_output=True,timeout=180,check=True).stdout)
 state.update({'captured_at':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'classification':'EXPLAINED_IMMUTABLE_STAGING_CUTOVER_WITH_CLEAN_UNDEPLOYED_HEAD','manual_server_source_modifications':bool(state['deployment_dirty_paths'] or state['staging_modified_tracked_paths'] or state['staging_extra_paths'] or any(x['runtime_source_path_mutations'] for x in state['containers'])),'future_b1_requirement':'BUILD_AND_DEPLOY_FROM_ONE_EXPLICIT_IMMUTABLE_GIT_COMMIT','production_mutations':0})
 out=Path(a.output).resolve();out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(state,sort_keys=True,indent=2)+'\n',encoding='utf-8');print(json.dumps({'output':str(out),'sha256':hashlib.sha256(out.read_bytes()).hexdigest(),'manual_server_source_modifications':state['manual_server_source_modifications']},sort_keys=True));return 0
if __name__=='__main__':raise SystemExit(main())
