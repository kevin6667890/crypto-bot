import type { EvaluationTreeNode } from "./types";
import { conditionTone, formatObserved } from "./state";

export function EvaluationTree({ node, zh }: { node: EvaluationTreeNode; zh: boolean }) {
  if (node.node_type === "CONDITION") return <div className="tracking-expression-condition" data-node="CONDITION">
    <span>{node.feature.replace(/_/g, " ")} <small>{node.operator} {String(node.value)}</small></span>
    <strong>{formatObserved(node.observed_value)}</strong><b className={`condition-badge ${conditionTone(node.state)}`}>{node.state}</b>
    {node.limitation && <small>{zh ? "当前数据不足或已过期" : "Current data is unavailable or stale"}</small>}
  </div>;
  const label = node.node_type === "ALL" ? (zh ? "全部满足" : "ALL") : node.node_type === "ANY" ? (zh ? "至少一项满足" : "ANY") : "NOT";
  return <div className={`tracking-expression-group ${node.node_type.toLowerCase()}`} data-node={node.node_type}>
    <header><strong>{label}</strong><b className={`condition-badge ${conditionTone(node.state)}`}>{node.state}</b></header>
    {node.children.map((child) => <EvaluationTree key={child.node_id} node={child} zh={zh} />)}
  </div>;
}

export function V2Delta({ delta, zh }: { delta: { overall_change?: { from: string; to: string } | null;
  leaf_changes?: Array<{ node_id: string; feature: string; from: string; to: string }>;
  group_changes?: Array<{ node_id: string; node_type: string; from: string; to: string }> }; zh: boolean }) {
  return <div className="delta-list expression-delta">
    {delta.overall_change && <p><b>{zh ? "整体" : "OVERALL"}</b><span>{delta.overall_change.from} → {delta.overall_change.to}</span></p>}
    {delta.leaf_changes?.map((item) => <p key={item.node_id}><b>{item.feature.replace(/_/g, " ")}</b><span>{item.from} → {item.to}</span></p>)}
    {delta.group_changes?.map((item) => <p key={item.node_id}><b>{item.node_type} · {item.node_id}</b><span>{item.from} → {item.to}</span></p>)}
  </div>;
}
