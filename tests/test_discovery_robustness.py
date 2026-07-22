from dashboard.discovery_robustness import generate_parameter_neighbors, generate_cost_scenarios, select_robustness_candidates, build_robustness_scenario_identity

P={"fast_period":20,"slow_period":100,"fast_ma_type":"EMA","atr_period":14,"volume_enabled":True,"minimum_volume_ratio":1.0,"maximum_distance":.004}
def test_neighbors_are_deterministic_one_change_and_input_safe():
    before=dict(P); one=generate_parameter_neighbors("TREND_PULLBACK",P); two=generate_parameter_neighbors("TREND_PULLBACK",dict(reversed(list(P.items()))))
    assert P==before and one==two and len({x['parameter_hash'] for x in one})==len(one)
    for item in one:
        keys=set(P)|set(item['parameters']); changed={k for k in keys if P.get(k)!=item['parameters'].get(k)}
        assert len(changed)==1 or changed=={'volume_enabled','minimum_volume_ratio'}
def test_cost_scenarios_are_ordered_and_do_not_mutate_input():
    config={"trading_fee":0.001,"slippage":0.002}; scenarios=generate_cost_scenarios(config)
    assert [x['scenario_name'] for x in scenarios]==['FEE_1_5X','SLIPPAGE_1_5X','COMBINED_2X','COMBINED_3X'] and config=={"trading_fee":.001,"slippage":.002}
def test_selection_is_eligible_rank_deterministic_and_capped():
    rows=[{'id':1,'eligibility_status':'ELIGIBLE','pareto_rank':1,'eligible_rank':2},{'id':2,'eligibility_status':'ELIGIBLE','pareto_rank':2,'eligible_rank':1},{'id':3,'eligibility_status':'REJECTED','pareto_rank':1,'eligible_rank':0}]
    assert [x['id'] for x in select_robustness_candidates(rows,2,2)]==[2,1]
def test_identity_distinguishes_zero_cost_scenarios():
    common=dict(category='COST_STRESS',source_parameter_hash='p',scenario_parameter_hash='p',source_execution_hash='e',scenario_execution_hash='e',instrument='BTC-USDT',timeframe='15m',dataset_fingerprint='d',five_fold_policy_version='f',scenario_policy_version='c')
    assert build_robustness_scenario_identity(scenario_name='FEE_1_5X',assumptions={'fee_multiplier':1.5},**common)!=build_robustness_scenario_identity(scenario_name='SLIPPAGE_1_5X',assumptions={'slippage_multiplier':1.5},**common)
