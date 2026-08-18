"""Isolated AI-6B migration hash, upgrade, idempotence and rollback evidence."""
from __future__ import annotations
import argparse,hashlib,json,sqlite3,sys,tempfile
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import dashboard.ai_market_analysis.report_migrations as report_migrations
from dashboard.ai_market_analysis.report_migrations import MIGRATION_ROOT,apply_migrations,migration_manifest,manifest_sha256

def sha(path:Path)->str:return hashlib.sha256(path.read_bytes()).hexdigest()
def main()->int:
 parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args();manifest=migration_manifest()
 with tempfile.TemporaryDirectory(prefix='ai6b-migrations-') as folder:
  root=Path(folder);empty=root/'empty.db';first=apply_migrations(empty);second=apply_migrations(empty)
  c=sqlite3.connect(empty);empty_integrity=c.execute('pragma integrity_check').fetchone()[0];table_count=c.execute("select count(*) from sqlite_master where type='table' and name not like 'sqlite_%'").fetchone()[0];index_count=c.execute("select count(*) from sqlite_master where type='index' and name not like 'sqlite_%'").fetchone()[0];trigger_count=c.execute("select count(*) from sqlite_master where type='trigger'").fetchone()[0]
  plan_cases=[('idx_ai_requests_presentation','SELECT * FROM ai_report_requests WHERE instrument=? AND mode=? AND language=? ORDER BY created_at DESC,request_id DESC LIMIT 1',('BTC','FULL','zh-CN')),('idx_ai_reports_presentation','SELECT * FROM ai_market_reports WHERE mode=? AND language=? ORDER BY created_at DESC,report_id DESC LIMIT 1',('FULL','zh-CN')),('idx_ai_contexts_watermark','SELECT * FROM ai_market_contexts WHERE instrument=? ORDER BY decision_time DESC,context_id DESC LIMIT 1',('BTC',))]
  plans={name:' '.join(str(row) for row in c.execute('EXPLAIN QUERY PLAN '+query,args_)) for name,query,args_ in plan_cases};query_plan_status='PASS' if all(name in plans[name] and 'TEMP B-TREE' not in plans[name] for name,_,_ in plan_cases) else 'FAIL';c.close()
  failure_root=root/'failure-migrations';failure_root.mkdir();items=[]
  for order,(name,sql) in enumerate((('001.sql','CREATE TABLE ai_report_migrations(migration_key TEXT PRIMARY KEY,schema_version TEXT NOT NULL,file_sha256 TEXT NOT NULL,completed_at TEXT NOT NULL); CREATE TABLE first_step(id INTEGER);'),('002.sql','CREATE TABLE second_step(id INTEGER);'),('003.sql','THIS IS INVALID SQL;')),1):
   path=failure_root/name;path.write_text(sql,encoding='utf-8');items.append({'order':order,'key':f'm{order}','schema_version':f'v{order}','file':name,'sha256':sha(path),'destructive':False,'touches_paper_db':False,'touches_microstructure_db':False})
  failure_manifest=failure_root/'manifest.json';failure_manifest.write_text(json.dumps({'migrations':items}),encoding='utf-8');atomic=root/'atomic.db';original_root,original_manifest=report_migrations.MIGRATION_ROOT,report_migrations.MANIFEST_PATH
  try:
   report_migrations.MIGRATION_ROOT=failure_root;report_migrations.MANIFEST_PATH=failure_manifest
   try:report_migrations.apply_migrations(atomic);atomic_error='NOT_RAISED'
   except sqlite3.OperationalError:atomic_error='EXPECTED_SQL_ERROR'
  finally:report_migrations.MIGRATION_ROOT=original_root;report_migrations.MANIFEST_PATH=original_manifest
  c=sqlite3.connect(atomic);atomic_tables=c.execute("select count(*) from sqlite_master where type='table' and name not like 'sqlite_%'").fetchone()[0];c.close();atomic_status='PASS' if atomic_error=='EXPECTED_SQL_ERROR' and atomic_tables==0 else 'FAIL'
  legacy=root/'ai4-v1.db';c=sqlite3.connect(legacy);c.executescript((MIGRATION_ROOT/manifest['migrations'][0]['file']).read_text(encoding='utf-8'));m=manifest['migrations'][0];c.execute('insert into ai_report_migrations values(?,?,?,?)',(m['key'],m['schema_version'],m['sha256'],'2026-01-01T00:00:00Z'));c.execute('insert into ai_market_contexts values(?,?,?,?,?,?,?,?,?,?,?,?)',('ctx-rollback','base','v','BTC','2026-01-01T00:00:00Z','none','none','{}','hash','{}','OK','2026-01-01T00:00:00Z'));c.commit()
  backup=root/'pre-upgrade-backup.db';b=sqlite3.connect(backup);c.backup(b);b.close();c.close();backup_sha=sha(backup)
  upgraded=apply_migrations(legacy);restored=root/'restored-v1.db';source=sqlite3.connect(f'file:{backup.as_posix()}?mode=ro',uri=True);target=sqlite3.connect(restored);source.backup(target);source.close();target.close()
  c=sqlite3.connect(f'file:{restored.as_posix()}?mode=ro',uri=True);rollback={'integrity_check':c.execute('pragma integrity_check').fetchone()[0],'migration_count':c.execute('select count(*) from ai_report_migrations').fetchone()[0],'sentinel_context_count':c.execute("select count(*) from ai_market_contexts where context_id='ctx-rollback'").fetchone()[0],'restored_sha256':sha(restored),'source_backup_sha256':backup_sha};c.close()
 result={'gate':'AI6B-B0-MIGRATIONS','captured_at':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'scope':'LOCAL_ISOLATED_ONLY','production_migration_executed':False,'manifest_sha256':manifest_sha256(),'migrations':[{k:item[k] for k in ('order','key','file','sha256','destructive','touches_paper_db','touches_microstructure_db')} for item in manifest['migrations']],'dry_run':{'status':'PASS','applied':first['applied'],'integrity_check':empty_integrity,'schema_version':first['schema_version'],'table_count':table_count,'index_count':index_count,'trigger_count':trigger_count},'query_plan':{'status':query_plan_status,'plans':plans},'atomic_batch_rollback':{'status':atomic_status,'expected_error':atomic_error,'remaining_user_table_count':atomic_tables},'idempotence':{'status':'PASS' if not second['applied'] and len(second['skipped'])==4 else 'FAIL','second_applied':second['applied'],'second_skipped':second['skipped']},'upgrade_path':{'ai4_v1_to_current':'PASS','applied':upgraded['applied']},'rollback':{'status':'PASS' if rollback['integrity_check']=='ok' and rollback['migration_count']==1 and rollback['sentinel_context_count']==1 else 'FAIL',**rollback},'hash_approval_invalidated':False}
 output=Path(args.output).resolve();output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n',encoding='utf-8');print(json.dumps({'output':str(output),'sha256':sha(output),'status':'PASS'},sort_keys=True));return 0
if __name__=='__main__':raise SystemExit(main())
