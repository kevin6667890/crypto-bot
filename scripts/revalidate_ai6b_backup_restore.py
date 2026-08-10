"""Exercise future AI SQLite WAL-safe backup and isolated restore locally."""
from __future__ import annotations
import argparse,hashlib,json,sqlite3,sys,tempfile
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from dashboard.ai_market_analysis.backup_restore import create_consistent_backup,verify_isolated_restore
from dashboard.ai_market_analysis.report_migrations import apply_migrations

def sha(path:Path)->str:return hashlib.sha256(path.read_bytes()).hexdigest()
def main()->int:
 parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
 with tempfile.TemporaryDirectory(prefix='ai6b-backup-source-') as source_folder,tempfile.TemporaryDirectory(prefix='ai6b-backup-offdir-') as backup_parent:
  source=Path(source_folder)/'ai_market_reports.db';apply_migrations(source)
  c=sqlite3.connect(source);c.execute('pragma journal_mode=WAL');c.execute("insert into ai_market_contexts values(?,?,?,?,?,?,?,?,?,?,?,?)",('backup-sentinel','base','v','BTC','2026-01-01T00:00:00Z','none','none','{}','hash','{}','OK','2026-01-01T00:00:00Z'));c.commit()
  wal_exists=Path(str(source)+'-wal').exists();backup=Path(backup_parent)/'backup';created=create_consistent_backup(source,backup,backup_id='ai6b-local-wal-safe-rehearsal');c.close();restored=verify_isolated_restore(backup)
  result={'gate':'AI6B-B0-BACKUP-RESTORE','captured_at':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'production_ai_database_status':'DATABASE_NOT_YET_PRESENT','production_database_backup_fabricated':False,'future_ai_sqlite_rehearsal':{'scope':'LOCAL_ISOLATED','source_wal_present_during_backup':wal_exists,'method':created['method'],'backup_outside_source_directory':source.parent!=backup.parent,'database_status':created['database_status'],'backup_artifacts':created['artifacts'],'manifest_sha256':created['manifest_sha256'],'restore':restored},'status':'PASS' if restored['integrity_check']=='ok' and restored['artifact_hashes_valid'] and restored['temporary_copy_deleted'] else 'FAIL'}
 output=Path(args.output).resolve();output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n',encoding='utf-8');print(json.dumps({'output':str(output),'sha256':sha(output),'status':result['status']},sort_keys=True));return 0
if __name__=='__main__':raise SystemExit(main())
