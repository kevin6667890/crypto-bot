from dashboard.reconciliation import reconcile

def trade(signal="s1",instrument="BTC-USDT",side="LONG",entry=100):
    return {"signal_id":signal,"instrument":instrument,"side":side,"entry":entry,"entry_price":entry,"expected_entry_price":entry,"signal_ts":100,"strategy_version":"v1","result_r":1,"exit_reason":"TAKE_PROFIT"}

def test_exact_and_divergence_classifications():
    exact=reconcile([trade()],[trade()]); assert exact["items"][0]["match_status"]=="Exact Match"; assert exact["drift_status"]=="Normal"
    mismatch=reconcile([{**trade(),"side":"SHORT"}],[trade()]); assert mismatch["items"][0]["match_status"]=="Decision Mismatch"
    entry=reconcile([{**trade(),"entry":110,"observed_entry_price":110}],[trade()]); assert entry["items"][0]["match_status"]=="Entry Divergence"

def test_missing_legacy_and_insufficient_data():
    assert reconcile([], [trade()])["items"][0]["match_status"]=="Paper Missing"
    assert reconcile([trade()], [])["items"][0]["match_status"]=="Backtest Missing"
    legacy={"signal_id":None,"instrument":"BTC-USDT","side":"LONG"}
    assert reconcile([legacy],[])["items"][0]["match_status"]=="Legacy Unmatched"
    assert reconcile([],[])["drift_status"]=="Insufficient Data"

