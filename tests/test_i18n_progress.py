import re
import time
from urllib.error import HTTPError
from pathlib import Path

from dashboard.job_queue import JobQueue
from dashboard.okx_history import OkxHistoryClient
from dashboard.portfolio_backtest import PortfolioParameters, run_portfolio_backtest
from dashboard.research_repository import ResearchRepository
from dashboard.strategy_rules import StrategyParameters

ROOT = Path(__file__).resolve().parents[1]

def candles(offset=0, count=80):
    return [{"ts":i*900,"open":100+offset+i*.1,"high":101+offset+i*.1,"low":99+offset+i*.1,"close":100.2+offset+i*.1,"volume":1000,"confirmed":1} for i in range(count)]

def test_job_progress_is_monotonic_and_structured(tmp_path):
    queue=JobQueue(tmp_path/"jobs.db",autostart=False);job=queue.enqueue("PORTFOLIO_BACKTEST",{"assets":["BTC-USDT"]})
    queue.checkpoint(job["id"],52,"portfolio.progress.processing_timestamps",{"processed":10,"total":20})
    queue.checkpoint(job["id"],40,"portfolio.progress.processing_timestamps",{"processed":11,"total":20})
    current=queue.get(job["id"]);assert current["progress"]==52;assert current["message_code"]=="portfolio.progress.processing_timestamps";assert current["message_params"]=={"processed":11,"total":20}

def test_terminal_job_semantics(tmp_path):
    queue=JobQueue(tmp_path/"terminal.db");queue.register("PORTFOLIO_BACKTEST",lambda *_:{"portfolio_run_id":7});job=queue.enqueue("PORTFOLIO_BACKTEST",{"case":"complete"})
    for _ in range(100):
        state=queue.get(job["id"])
        if state["status"]=="COMPLETED":break
        time.sleep(.01)
    assert (state["status"],state["progress"],state["message_code"])==("COMPLETED",100,"portfolio.progress.completed")
    failed=JobQueue(tmp_path/"failed.db");failed.register("BROKEN",lambda *_:(_ for _ in ()).throw(RuntimeError("real failure")));job=failed.enqueue("BROKEN",{})
    for _ in range(100):
        state=failed.get(job["id"])
        if state["status"]=="FAILED":break
        time.sleep(.01)
    assert state["status"]=="FAILED" and state["error"]=="real failure" and state["message_code"]=="job.failed"

def test_restart_interrupts_running_job_and_cancel_is_terminal(tmp_path):
    path=tmp_path/"restart.db";queue=JobQueue(path,autostart=False);job=queue.enqueue("X",{})
    with queue.connect() as connection:connection.execute("update research_jobs set status='RUNNING' where id=?",(job["id"],))
    recovered=JobQueue(path,autostart=False).get(job["id"])
    assert recovered["status"]=="INTERRUPTED" and recovered["message_code"]=="job.interrupted.restart"
    queued=JobQueue(tmp_path/"cancel.db",autostart=False);job=queued.enqueue("X",{});state=queued.cancel(job["id"])
    assert state["status"]=="CANCELLED" and state["message_code"]=="job.cancelled"

def test_okx_429_reports_retry(monkeypatch):
    calls=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*_):return False
        def read(self):return b'{"code":"0","data":[]}'
    attempts=iter([HTTPError("https://www.okx.com",429,"limited",{},None),Response()])
    def fake_open(*_args,**_kwargs):
        item=next(attempts)
        if isinstance(item,Exception):raise item
        return item
    monkeypatch.setattr("dashboard.okx_history.urlopen",fake_open);monkeypatch.setattr("dashboard.okx_history.time.sleep",lambda _delay:None)
    assert OkxHistoryClient._request({},lambda attempt,delay:calls.append((attempt,delay)))==[]
    assert calls and calls[0][0]==1

def test_portfolio_engine_reports_real_timestamp_work():
    updates=[];run_portfolio_backtest({"BTC-USDT":candles(),"ETH-USDT":candles(10),"SOL-USDT":candles(20)},StrategyParameters(),PortfolioParameters(),0,79*900,lambda fraction,code,params:updates.append((fraction,code,params)))
    processed=[x for x in updates if x[1]=="portfolio.progress.processing_timestamps"]
    assert processed and [x[0] for x in processed]==sorted(x[0] for x in processed)
    assert processed[-1][2]["processed"]<=processed[-1][2]["total"] and updates[-1][1]=="portfolio.progress.calculating_metrics"

def test_portfolio_persistence_can_checkpoint_without_sqlite_lock(tmp_path):
    path=tmp_path/"shared.db";repository=ResearchRepository(path);queue=JobQueue(path,autostart=False);job=queue.enqueue("PORTFOLIO_BACKTEST",{})
    run_id=repository.create_portfolio_run({"asset_weights":{"BTC-USDT":1}},job["id"])
    result={"trades":[{"instrument":"BTC-USDT","signal_id":"s1","entry_ts":1}],"equity":[{"ts":1,"equity":10000,"cash":10000}],"exposure_timeline":[{"ts":1,"gross":0}],"metrics":{}}
    repository.save_portfolio_result(run_id,result,lambda code,params,fraction:queue.checkpoint(job["id"],95+int(fraction*3),code,params))
    assert repository.portfolio_run(run_id)["status"]=="COMPLETED"
    assert queue.get(job["id"])["progress"]>=95

def test_i18n_uses_explicit_context_without_dom_mutation():
    source=(ROOT/"frontend/src/i18n.tsx").read_text(encoding="utf-8")
    assert "MutationObserver" not in source and "characterData" not in source
    assert "createContext" in source and "localStorage" in source and "type TranslationKey = keyof typeof en" in source
    for code in ("portfolio.progress.loading_candles","portfolio.progress.processing_timestamps","portfolio.progress.completed"):assert source.count(f'"{code}"')>=2

def test_async_progress_uses_message_code_and_recovers_by_job_id():
    source=(ROOT/"frontend/src/PortfolioResearch.tsx").read_text(encoding="utf-8")
    assert re.search(r"message\(\s*job\.message_code,\s*job\.message_params,\s*job\.progress_message\s*\)",source)
    assert "researchApi.job(job.id)" in source and '"CANCEL_REQUESTED"' in source and "setInterval" in source

def test_active_jsx_has_no_known_mixed_language_fragments_or_literal_accessibility_text():
    forbidden=("OKX public trades + SWAP OI","SHORT bias","confirmed trend alignment","Loading BTC-USDT confirmed candles","Rule gates rejected entry")
    for path in (ROOT/"frontend/src").rglob("*.tsx"):
        if path.name=="i18n.tsx":continue
        source=re.sub(r"/\*.*?\*/","",path.read_text(encoding="utf-8"),flags=re.S)
        assert not any(fragment in source for fragment in forbidden),path
        literals=re.findall(r'(?:placeholder|title|aria-label)="([A-Za-z][^"]*)"',source)
        assert not literals,(path,literals)

def test_portfolio_backend_progress_uses_codes_not_display_sentences():
    source=(ROOT/"dashboard/research_service.py").read_text(encoding="utf-8")
    portfolio=source[source.index("def _job_portfolio"):source.index("def reconciliation")]
    assert 'f"Loading ' not in portfolio
    assert '"portfolio.progress.loading_candles"' in portfolio
    assert '"portfolio.progress.rate_limited"' in portfolio

def _translation_blocks():
    source=(ROOT/"frontend/src/i18n.tsx").read_text(encoding="utf-8")
    en=source.split("const en = {",1)[1].split("} as const;",1)[0]
    zh=source.split("const zh: Record<TranslationKey, string> = {",1)[1].split("};",1)[0]
    return source,set(re.findall(r'"([^"]+)"\s*:',en)),set(re.findall(r'"([^"]+)"\s*:',zh))

def test_translation_catalogs_have_identical_keys_and_interpolation_params():
    source,en_keys,zh_keys=_translation_blocks()
    assert en_keys==zh_keys,(sorted(en_keys-zh_keys),sorted(zh_keys-en_keys))
    en=source.split("const en = {",1)[1].split("} as const;",1)[0]
    zh=source.split("const zh: Record<TranslationKey, string> = {",1)[1].split("};",1)[0]
    en_values=dict(re.findall(r'"([^"]+)"\s*:\s*"([^"]*)"',en));zh_values=dict(re.findall(r'"([^"]+)"\s*:\s*"([^"]*)"',zh))
    for key in en_keys:
        assert set(re.findall(r"\{(\w+)\}",en_values[key]))==set(re.findall(r"\{(\w+)\}",zh_values[key])),key

def test_active_components_have_no_untranslated_visible_english_sentences():
    active=[ROOT/"frontend/src/App.tsx",ROOT/"frontend/src/PortfolioResearch.tsx",ROOT/"frontend/src/StrategyResearch.tsx",ROOT/"frontend/src/Operations.tsx",ROOT/"frontend/src/ReconciliationPanel.tsx",ROOT/"frontend/src/ResearchCharts.tsx"]
    active += list((ROOT/"frontend/src/validation").glob("*.tsx"))+list((ROOT/"frontend/src/shadow").glob("*.tsx"))+list((ROOT/"frontend/src/lifecycle").glob("*.tsx"))
    allowed=re.compile(r"^(?:Crypto-Bot|OKX|DeepSeek|SQLite|AI|API|HTTP|CSV|IS|OOS|PF|P&L|R|SL|TP|MA\d+(?:\s*/\s*MA\d+)?|EMA\d+|RSI|ATR|CVD|OI|Bootstrap|[A-Z]{2,}(?:-[A-Z]+)?|\d+[mHdD])$",re.I)
    failures=[]
    for path in active:
        source=re.sub(r"/\*.*?\*/","",path.read_text(encoding="utf-8"),flags=re.S)
        for text in re.findall(r">\s*([A-Za-z][A-Za-z0-9 &/·:+.()_-]{2,})\s*</",source):
            value=" ".join(text.split())
            if len(re.findall(r"[A-Za-z]+",value))>=2 and not allowed.fullmatch(value):failures.append((path.name,value))
        for value in re.findall(r'(?:placeholder|title|aria-label)="([^"]+)"',source):
            if re.search(r"[A-Za-z]",value) and not allowed.fullmatch(value):failures.append((path.name,value))
    assert not failures,failures

def test_remaining_named_i18n_risks_use_explicit_keys_and_dynamic_codes():
    strategy=(ROOT/"frontend/src/StrategyResearch.tsx").read_text(encoding="utf-8")
    assert re.search(r"message\(\s*run\?\.message_code,\s*run\?\.message_params,\s*run\?\.progress_message\s*\)",strategy)
    for raw in ("Research API unavailable.","Could not refresh backtest status.","Backtest could not start.","Strategy save failed.","Walk-forward queued as job #","My Strategy"):
        assert raw not in strategy
    operations=(ROOT/"frontend/src/Operations.tsx").read_text(encoding="utf-8")
    for key in ("operations.version","operations.gitCommit","operations.uptime","operations.disk","operations.memory"):assert f't("{key}")' in operations
    reconciliation=(ROOT/"frontend/src/ReconciliationPanel.tsx").read_text(encoding="utf-8")
    for raw in ('"Signal ID"','"Paper / Backtest"','"Entry Δ"','"Exit"'):assert raw not in reconciliation
    near=(ROOT/"frontend/src/validation/NearMissPanel.tsx").read_text(encoding="utf-8")
    assert "selected.what_prevented_entry" not in near and "selected.what_would_have_changed" not in near and "x.failed_gates.join" not in near
    sensitivity=(ROOT/"frontend/src/validation/SensitivityLab.tsx").read_text(encoding="utf-8")
    assert "parameterLabels" in sensitivity and "label_codes || x.labels" in sensitivity

def test_single_asset_and_phase4_job_progress_are_structured():
    research=(ROOT/"dashboard/research_service.py").read_text(encoding="utf-8")
    repository=(ROOT/"dashboard/research_repository.py").read_text(encoding="utf-8")
    validation=(ROOT/"dashboard/validation_service.py").read_text(encoding="utf-8")
    for code in ("research.progress.checking_cache","research.progress.running_backtest","research.progress.saving_results","research.progress.walk_forward_window"):assert code in research
    assert 'self._ensure_column(connection, "backtest_runs", "message_code", "TEXT")' in repository
    assert 'self._ensure_column(connection, "backtest_runs", "message_params", "TEXT")' in repository
    for code in ("validation.progress.loading_decisions","validation.progress.testing_combination","validation.progress.running_benchmark","validation.progress.bootstrap"):assert code in validation
