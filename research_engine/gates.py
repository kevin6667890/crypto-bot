from __future__ import annotations
from dataclasses import dataclass
from typing import Any
@dataclass(frozen=True)
class GateConfig:
    minimum_trades:int=30; maximum_drawdown:float=25.; minimum_oos_sharpe:float=0.; maximum_cost_ratio:float=1.; minimum_profitable_fold_ratio:float=.6; minimum_parameter_stability:float=.6; minimum_cross_asset_consistency:float=.34; minimum_cost_stress_survival:float=.5
def evaluate_gates(metrics:dict[str,Any], config:GateConfig=GateConfig())->list[dict[str,Any]]:
    checks=[("MINIMUM_TRADES",metrics.get("total_trades"),lambda x:x>=config.minimum_trades),("MAXIMUM_DRAWDOWN",metrics.get("maximum_drawdown"),lambda x:x<=config.maximum_drawdown),("MINIMUM_OOS_SHARPE",metrics.get("oos_sharpe"),lambda x:x>=config.minimum_oos_sharpe),("MAXIMUM_COST_RATIO",metrics.get("cost_ratio"),lambda x:x<=config.maximum_cost_ratio),("PROFITABLE_FOLD_RATIO",metrics.get("profitable_fold_ratio"),lambda x:x>=config.minimum_profitable_fold_ratio),("PARAMETER_STABILITY",metrics.get("parameter_stability"),lambda x:x>=config.minimum_parameter_stability),("CROSS_ASSET_CONSISTENCY",metrics.get("cross_asset_consistency"),lambda x:x>=config.minimum_cross_asset_consistency),("COST_STRESS_SURVIVAL",metrics.get("cost_stress_survival"),lambda x:x>=config.minimum_cost_stress_survival)]
    reasons={"PARAMETER_STABILITY":"PARAMETER_INSTABILITY","COST_STRESS_SURVIVAL":"COST_FRAGILE"}
    return [{"gate":name,"status":"UNAVAILABLE" if value is None else "PASS" if test(value) else "FAIL","observed":value,"rejection_reason":None if value is None or test(value) else reasons.get(name,name)} for name,value,test in checks]
