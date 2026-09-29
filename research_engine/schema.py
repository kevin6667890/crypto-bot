from __future__ import annotations
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib, json
from typing import Any

def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
def digest(value: Any) -> str: return hashlib.sha256(canonical(value).encode()).hexdigest()
def now() -> str: return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

@dataclass(frozen=True)
class Hypothesis:
    hypothesis_id: str; research_cycle_id: str; family: str; description: str; market_intuition: str
    required_features: tuple[str,...]; assets: tuple[str,...]; timeframes: tuple[str,...]
    parameter_space: dict[str,Any]; entry_logic_version: str; exit_logic_version: str
    execution_model: str; data_requirements: tuple[str,...]; dataset_version: str; code_version: str
    created_at: str = field(default_factory=now)
    def deterministic(self) -> dict[str,Any]:
        data=asdict(self); data.pop("created_at",None); return data
    def serialize(self) -> str: return canonical(asdict(self))

@dataclass(frozen=True)
class Candidate:
    candidate_id: str; hypothesis_id: str; family: str; parameters: dict[str,Any]; feature_set: tuple[str,...]
    asset: str; timeframe: str; dataset_version: str; code_version: str; execution_assumptions: dict[str,Any]

def candidate_id(*, hypothesis: Hypothesis, parameters: dict[str,Any], feature_set: tuple[str,...], asset: str, timeframe: str, execution_assumptions: dict[str,Any]) -> str:
    return "candidate:"+digest({"logic":hypothesis.entry_logic_version,"exit":hypothesis.exit_logic_version,"hypothesis":hypothesis.deterministic(),"parameters":parameters,"features":sorted(feature_set),"asset":asset,"timeframe":timeframe,"dataset":hypothesis.dataset_version,"code":hypothesis.code_version,"execution":execution_assumptions})

@dataclass(frozen=True)
class ResearchCycle:
    research_cycle_id: str; dataset_version: str; development: tuple[int,int]; oos: tuple[int,int]; holdout: tuple[int,int]; oot: tuple[int,int]; policy_version: str="research-engine-v1"
