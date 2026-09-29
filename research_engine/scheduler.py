"""Fail-closed Stage 0–6 scheduler; runners are injected adapters."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable
from .features import FeatureRegistry
from .gates import GateConfig, evaluate_gates
from .schema import Candidate, Hypothesis

@dataclass
class StageResult:
    stage:int; status:str; metrics:dict[str,Any]; gates:list[dict[str,Any]]; reason:str|None=None

class ResearchEngine:
    """Thin orchestrator. It never generates candidates from holdout results."""
    def __init__(self, ledger, gates:GateConfig=GateConfig(), features:FeatureRegistry|None=None): self.ledger,self.gates,self.features=ledger,gates,features or FeatureRegistry()
    def stage0(self,h:Hypothesis)->StageResult:
        unavailable=[x.feature for x in self.features.check((*h.required_features,*h.data_requirements)) if x.status=="UNAVAILABLE"]
        status="UNAVAILABLE" if unavailable else "PASS"; reason=",".join(unavailable) if unavailable else None
        return StageResult(0,status,{},[],reason)
    def evaluate(self, candidate:Candidate, hypothesis:Hypothesis, stage:int, runner:Callable[[Candidate,int],dict[str,Any]])->StageResult:
        if stage==0: result=self.stage0(hypothesis)
        else:
            metrics=runner(candidate,stage); gates=evaluate_gates(metrics,self.gates)
            needed={1:{"MINIMUM_TRADES","MAXIMUM_DRAWDOWN"},2:{"PARAMETER_STABILITY"},3:{"MINIMUM_OOS_SHARPE","PROFITABLE_FOLD_RATIO"},4:{"CROSS_ASSET_CONSISTENCY"},5:{"COST_STRESS_SURVIVAL","MAXIMUM_COST_RATIO"},6:set()}.get(stage,set())
            failures=[x["rejection_reason"] or x["gate"]+"_UNAVAILABLE" for x in gates if x["gate"] in needed and x["status"]!="PASS"]
            result=StageResult(stage,"PASS" if not failures else "FAIL",metrics,gates,";".join(failures) or None)
        self.ledger.append_experiment({"research_run_id": hypothesis.research_cycle_id, "research_cycle_id": hypothesis.research_cycle_id,
            "hypothesis_id": hypothesis.hypothesis_id, "candidate_id": candidate.candidate_id, "family":candidate.family,
            "asset":candidate.asset,"timeframe":candidate.timeframe,"dataset_version":candidate.dataset_version,"code_version":candidate.code_version,
            "stage":f"STAGE_{stage}","status":result.status,"gate_result":result.gates,"rejection_reason":result.reason,
            "parameters":candidate.parameters,"feature_set":candidate.feature_set,"execution_assumptions":candidate.execution_assumptions,"metrics":result.metrics})
        return result
    def run_candidate(self,candidate:Candidate,hypothesis:Hypothesis,runner:Callable[[Candidate,int],dict[str,Any]])->list[StageResult]:
        results=[]
        for stage in range(7):
            result=self.evaluate(candidate,hypothesis,stage,runner); results.append(result)
            if result.status!="PASS": break
        return results
