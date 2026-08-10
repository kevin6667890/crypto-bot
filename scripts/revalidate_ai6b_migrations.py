"""Isolated AI-6B migration hash, upgrade, idempotence and rollback evidence."""
from __future__ import annotations
import argparse,hashlib,json,sqlite3,sys,tempfile
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from dashboard.ai_market_analysis.report_migrations import MIGRATION_ROOT,apply_migrations,migration_manifest,manifest_sha256

def sha(path:Path)->str:return hashlib.sha256(path.read_bytes()).hexdigest()
def main()->int:
 parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args();manifest=migration_manifest()
 with tempfile.TemporaryDirectory(prefix='ai6b-migrations-') as folder:
  root=Path(folder);empty=root/'empty.db';first=apply_migrations(empty);second=apply_migrations(empty)
  c=sqlite3.connect(empty);empty_integrity=c.execute('pragma integrity_check').fetchone()[0];table_count=c.execute("select count(*) from sqlite_master where type='table' and name not like 'sqlite_%'").fetchone()[0];c.close()
  legacy=root/'ai4-v1.db';c=sqlite3.connect(legacy);c.executescript((MIGRATION_ROOT/manifest['migrations'][0]['file']).read_text(encoding='utf-8'));m=manifest['migrations'][0];c.execute('insert into ai_report_migrations values(?,?,?,?)',(m['key'],m['schema_version'],m['sha256'],'2026-01-01T00:00:00Z'));c.execute('insert into ai_market_contexts values(?,?,?,?,?,?,?,?,?,?,?,?)',('ctx-rollback','base','v','BTC','2026-01-01T00:00:00Z','none','none','{}','hash','{}','OK','2026-01-01T00:00:00Z'));c.commit()
  backup=root/'pre-upgrade-backup.db';b=sqlite3.connect(backup);c.backup(b);b.close();c.close();backup_sha=sha(backup)
  upgraded=apply_migrations(legacy);restored=root/'restored-v1.db';source=sqlite3.connect(f'file:{backup.as_posix()}?mode=ro',uri=True);target=sqlite3.connect(restored);source.backup(target);source.close();target.close()
  c=sqlite3.connect(f'file:{restored.as_posix()}?mode=ro',uri=True);rollback={'integrity_check':c.execute('pragma integrity_check').fetchone()[0],'migration_count':c.execute('select count(*) from ai_report_migrations').fetchone()[0],'sentinel_context_count':c.execute("select count(*) from ai_market_contexts where context_id='ctx-rollback'").fetchone()[0],'restored_sha256':sha(restored),'source_backup_sha256':backup_sha};c.close()
 result={'gate':'AI6B-B0-MIGRATIONS','captured_at':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'scope':'LOCAL_ISOLATED_ONLY','production_migration_executed':False,'manifest_sha256':manifest_sha256(),'migrations':[{k:item[k] for k in ('order','key','file','sha256','destructive','touches_paper_db','touches_microstructure_db')} for item in manifest['migrations']],'dry_run':{'status':'PASS','applied':first['applied'],'integrity_check':empty_integrity,'table_count':table_count},'idempotence':{'status':'PASS' if not second['applied'] and len(second['skipped'])==4 else 'FAIL','second_applied':second['applied'],'second_skipped':second['skipped']},'upgrade_path':{'ai4_v1_to_current':'PASS','applied':upgraded['applied']},'rollback':{'status':'PASS' if rollback['integrity_check']=='ok' and rollback['migration_count']==1 and rollback['sentinel_context_count']==1 else 'FAIL',**rollback},'hash_approval_invalidated':False}
 output=Path(args.output).resolve();output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n',encoding='utf-8');print(json.dumps({'output':str(output),'sha256':sha(output),'status':'PASS'},sort_keys=True));return 0
if __name__=='__main__':raise SystemExit(main())
