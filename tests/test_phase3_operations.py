import sqlite3, threading, time
from pathlib import Path
from dashboard.alert_service import AlertService
from dashboard.job_queue import JobQueue
from dashboard.rate_limit import RateLimiter
from dashboard.health_service import HealthService

def test_alert_deduplication_acknowledgement_and_resolution(tmp_path:Path):
    service=AlertService(tmp_path/"ops.db")
    first=service.raise_alert("Collector Stale","warning","collector","stale","BTC-USDT",key="stale-btc")
    second=service.raise_alert("Collector Stale","warning","collector","still stale","BTC-USDT",key="stale-btc")
    assert first["id"]==second["id"] and second["occurrence_count"]==2
    assert service.acknowledge(first["id"])
    assert service.resolve("stale-btc")
    assert service.list()[0]["status"]=="resolved"

def test_rate_limit_returns_denial_after_limit():
    limiter=RateLimiter()
    assert limiter.allow("chat","ip",2,60)[0]
    assert limiter.allow("chat","ip",2,60)[0]
    allowed,retry=limiter.allow("chat","ip",2,60)
    assert not allowed and retry>0

def test_job_queue_dedupe_limit_cancel_retry_and_restart_recovery(tmp_path:Path):
    db=tmp_path/"jobs.db"; queue=JobQueue(db,max_queue=2,autostart=False)
    first=queue.enqueue("TEST",{"x":1},"ip"); duplicate=queue.enqueue("TEST",{"x":1},"ip")
    assert first["id"]==duplicate["id"] and duplicate["deduplicated"]
    second=queue.enqueue("TEST",{"x":2},"ip")
    try: queue.enqueue("TEST",{"x":3},"ip"); assert False
    except OverflowError: pass
    assert queue.cancel(second["id"])["status"]=="CANCELLED"
    retried=queue.retry(second["id"],"ip"); assert retried["retry_of_job_id"]==second["id"]
    with queue.connect() as c:c.execute("UPDATE research_jobs SET status='RUNNING' WHERE id=?",(first["id"],))
    restarted=JobQueue(db,max_queue=2,autostart=False)
    assert restarted.get(first["id"])["status"]=="INTERRUPTED"

def test_single_worker_failure_does_not_block_next_job(tmp_path:Path):
    queue=JobQueue(tmp_path/"worker.db",max_queue=5,autostart=False); active=0; maximum=0; lock=threading.Lock()
    def handler(job_id,payload,checkpoint):
        nonlocal active,maximum
        with lock:active+=1;maximum=max(maximum,active)
        try:
            if payload.get("fail"):raise ValueError("expected")
            time.sleep(.03);return {"ok":True}
        finally:
            with lock:active-=1
    queue.register("TEST",handler); bad=queue.enqueue("TEST",{"fail":True},"a"); good=queue.enqueue("TEST",{"fail":False},"b"); queue.worker.start()
    deadline=time.time()+2
    while time.time()<deadline and queue.get(good["id"])["status"] not in {"COMPLETED","FAILED"}:time.sleep(.02)
    queue._stop.set()
    assert queue.get(bad["id"])["status"]=="FAILED" and queue.get(good["id"])["status"]=="COMPLETED" and maximum==1

def test_health_is_sanitized_and_deepseek_is_boolean(tmp_path:Path,monkeypatch):
    db=tmp_path/"paper.db"
    with sqlite3.connect(db) as c:c.execute("create table sample(id integer)")
    class Paper:
        last_analysis={"BTC-USDT":{"updated_at":None},"ETH-USDT":{"updated_at":None},"SOL-USDT":{"updated_at":None}}
        scheduler_running=True
    class Jobs:
        def list(self,_limit):return []
    monkeypatch.setenv("DEEPSEEK_API_KEY","super-secret-value")
    payload=HealthService(db,Paper(),Jobs(),AlertService(db),tmp_path).payload(True)
    rendered=str(payload)
    assert payload["deepseek_configured"] is True
    assert "super-secret-value" not in rendered and str(db) not in rendered and ".env" not in rendered
