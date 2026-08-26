import { Plus, Trash2 } from "lucide-react";
import type { Language } from "../i18n";
import type { ThesisCapabilities, ThesisExpressionV2, ThesisFeatureCapability, ThesisPresetAssumption } from "./types";

export const EXPRESSION_MAX_DEPTH = 3;
export const EXPRESSION_MAX_LEAVES = 10;

const operators: Record<string, string> = { gt: ">", gte: "≥", lt: "<", lte: "≤", eq: "=" };

export function featureAvailable(feature: ThesisFeatureCapability, mode: "historical" | "current") {
  const explicit = mode === "historical" ? feature.historical_availability : feature.current_availability;
  return explicit ? explicit === "AVAILABLE" : feature.availability === "AVAILABLE";
}

export function expressionDepth(node: ThesisExpressionV2): number {
  if (node.node_type === "CONDITION") return 1;
  if (node.node_type === "NOT") return 1 + expressionDepth(node.child);
  if (node.node_type === "SEQUENCE") return 1 + Math.max(...node.steps.map(expressionDepth));
  return 1 + Math.max(...node.children.map(expressionDepth));
}

export function expressionLeaves(node: ThesisExpressionV2): number {
  if (node.node_type === "CONDITION") return 1;
  if (node.node_type === "NOT") return expressionLeaves(node.child);
  if (node.node_type === "SEQUENCE") return node.steps.reduce((total, step) => total + expressionLeaves(step), 0);
  return node.children.reduce((total, child) => total + expressionLeaves(child), 0);
}

export function expressionFeatures(node: ThesisExpressionV2): string[] {
  if (node.node_type === "CONDITION") return [node.feature];
  if (node.node_type === "NOT") return expressionFeatures(node.child);
  if (node.node_type === "SEQUENCE") return [...new Set(node.steps.flatMap(expressionFeatures))];
  return [...new Set(node.children.flatMap(expressionFeatures))];
}

export function expressionIsTrackable(node: ThesisExpressionV2, capabilities: ThesisCapabilities): boolean {
  if (node.node_type === "SEQUENCE") return false; // V3.1 ships historical-only sequence evaluation.
  return expressionFeatures(node).every((code) => {
    const feature = capabilities.features.find((item) => item.code === code);
    return !!feature && featureAvailable(feature, "current");
  });
}

export function expressionIsValid(node: ThesisExpressionV2, capabilities: ThesisCapabilities, timeframe: string): boolean {
  if (expressionDepth(node) > EXPRESSION_MAX_DEPTH || expressionLeaves(node) > EXPRESSION_MAX_LEAVES) return false;
  if (node.node_type === "NOT") return expressionIsValid(node.child, capabilities, timeframe);
  if (node.node_type === "SEQUENCE") return node.steps.length >= 2 && node.steps.length <= 3
    && Number.isInteger(node.max_gap_bars) && node.max_gap_bars >= 1 && node.max_gap_bars <= 500
    && node.steps.every((step) => step.node_type !== "SEQUENCE" && expressionIsValid(step, capabilities, timeframe));
  if (node.node_type !== "CONDITION") return node.children.length >= 2 && node.children.length <= 8
    && node.children.every((child) => expressionIsValid(child, capabilities, timeframe));
  const feature = capabilities.features.find((item) => item.code === node.feature);
  if (!feature || !featureAvailable(feature, "historical") || !feature.supported_timeframes.includes(timeframe)
      || !feature.operators.includes(node.operator)) return false;
  if (feature.value_type === "number" && (typeof node.value !== "number" || !Number.isFinite(node.value))) return false;
  if (feature.value_type === "boolean" && typeof node.value !== "boolean") return false;
  if (Object.keys(node.parameters || {}).some((name) => !(name in (feature.parameters || {})))) return false;
  return Object.entries(feature.parameters || {}).every(([name, contract]) => {
    const value = node.parameters?.[name];
    if (contract.required && value === undefined) return false;
    if (typeof value === "number" && ((contract.minimum != null && value < contract.minimum) || (contract.maximum != null && value > contract.maximum))) return false;
    return true;
  });
}

export function defaultCondition(features: ThesisFeatureCapability[], timeframe: string): ThesisExpressionV2 {
  const feature = features.find((item) => featureAvailable(item, "historical") && item.supported_timeframes.includes(timeframe))
    || features.find((item) => featureAvailable(item, "historical")) || features[0];
  if (!feature) return { node_type: "CONDITION", feature: "", operator: "", value: 0, parameters: {} };
  const parameters = Object.fromEntries(Object.entries(feature.parameters || {}).flatMap(([name, contract]) =>
    contract.default !== undefined ? [[name, contract.default]] : contract.required && contract.minimum != null ? [[name, contract.minimum]] : []));
  return { node_type: "CONDITION", feature: feature.code, operator: feature.operators[0] || "eq",
    value: feature.value_type === "boolean" ? true : feature.bounds.minimum ?? 0, parameters };
}

export function expressionLabel(node: ThesisExpressionV2, capabilities: ThesisCapabilities | null, language: Language): string {
  if (node.node_type === "ALL") return node.children.map((child) => expressionLabel(child, capabilities, language)).join(language === "zh" ? " 且 " : " AND ");
  if (node.node_type === "ANY") return `(${node.children.map((child) => expressionLabel(child, capabilities, language)).join(language === "zh" ? " 或 " : " OR ")})`;
  if (node.node_type === "NOT") return `${language === "zh" ? "非" : "NOT"} (${expressionLabel(node.child, capabilities, language)})`;
  if (node.node_type === "SEQUENCE") return node.steps.map((step, index) => `${language === "zh" ? `步骤 ${index + 1}` : `Step ${index + 1}`}: ${expressionLabel(step, capabilities, language)}`).join(" → ");
  const feature = capabilities?.features.find((item) => item.code === node.feature);
  const params = Object.values(node.parameters || {}).length ? ` (${Object.entries(node.parameters || {}).map(([key, value]) => `${key}=${value}`).join(", ")})` : "";
  return `${feature?.label[language] || node.feature} ${operators[node.operator] || node.operator} ${String(node.value)}${params}`;
}

type ExpressionProps = { node: ThesisExpressionV2; capabilities: ThesisCapabilities; language: Language; editable?: boolean;
  timeframe?: string; depth?: number; onChange?: (next: ThesisExpressionV2) => void; onRemove?: () => void };

export function ExpressionTree({ node, capabilities, language, editable = false, timeframe = capabilities.timeframes[0] || "4H", depth = 1, onChange, onRemove }: ExpressionProps) {
  const zh = language === "zh";
  if (node.node_type === "CONDITION") {
    const feature = capabilities.features.find((item) => item.code === node.feature);
    const allowed = capabilities.features.filter((item) => featureAvailable(item, "historical") && item.supported_timeframes.includes(timeframe));
    const updateFeature = (code: string) => onChange?.(defaultCondition(allowed.filter((item) => item.code === code), timeframe));
    return <div className="expression-condition" data-node="CONDITION">
      {editable ? <select aria-label={zh ? "特征" : "Feature"} value={node.feature} onChange={(event) => updateFeature(event.target.value)}>
        {allowed.map((item) => <option value={item.code} key={item.code}>{item.label[language]}</option>)}</select>
        : <strong>{feature?.label[language] || node.feature}</strong>}
      {editable ? <select aria-label={zh ? "运算符" : "Operator"} value={node.operator} onChange={(event) => onChange?.({ ...node, operator: event.target.value })}>
        {feature?.operators.map((operator) => <option value={operator} key={operator}>{operators[operator] || operator}</option>)}</select>
        : <span>{operators[node.operator] || node.operator}</span>}
      {feature?.value_type === "boolean" ? (editable ? <select aria-label={zh ? "布尔值" : "Boolean value"} value={String(node.value)} onChange={(event) => onChange?.({ ...node, value: event.target.value === "true" })}><option value="true">TRUE</option><option value="false">FALSE</option></select> : <span>{String(node.value).toUpperCase()}</span>)
        : editable ? <input aria-label={zh ? "阈值" : "Threshold"} type="number" value={String(node.value)} min={feature?.bounds.minimum ?? undefined} max={feature?.bounds.maximum ?? undefined} onChange={(event) => onChange?.({ ...node, value: Number(event.target.value) })} /> : <span>{String(node.value)}</span>}
      {Object.entries(feature?.parameters || {}).map(([name, contract]) => <label className="expression-parameter" key={name}><small>{name.replace(/_/g, " ")}</small>
        <input disabled={!editable} aria-label={name} type={contract.value_type === "integer" || contract.value_type === "number" ? "number" : "text"}
          min={contract.minimum ?? undefined} max={contract.maximum ?? undefined} value={String(node.parameters?.[name] ?? "")}
          onChange={(event) => onChange?.({ ...node, parameters: { ...node.parameters, [name]: contract.value_type === "integer" || contract.value_type === "number" ? Number(event.target.value) : event.target.value } })} /></label>)}
      {editable && onChange && depth < EXPRESSION_MAX_DEPTH && <button type="button" className="secondary-btn compact" onClick={() => onChange({ node_type: "NOT", child: node })}>NOT</button>}
      {editable && onRemove && <button type="button" className="icon-button" aria-label={zh ? "删除条件" : "Remove condition"} onClick={onRemove}><Trash2 size={15} /></button>}
    </div>;
  }
  if (node.node_type === "NOT") return <div className="expression-group not" data-node="NOT"><header><strong>NOT</strong>{editable && <button type="button" className="secondary-btn compact" onClick={() => onChange?.(node.child)}>{zh ? "取消 NOT" : "Remove NOT"}</button>}</header>
    <ExpressionTree node={node.child} capabilities={capabilities} language={language} editable={editable} timeframe={timeframe} depth={depth + 1} onChange={(child) => onChange?.({ ...node, child })} /></div>;
  if (node.node_type === "SEQUENCE") return <div className="expression-group sequence" data-node="SEQUENCE"><header><strong>{zh ? "顺序" : "SEQUENCE"}</strong></header>
    {node.steps.map((step, index) => <div key={index}><small>{zh ? `步骤 ${index + 1}` : `Step ${index + 1}`}</small><ExpressionTree node={step} capabilities={capabilities} language={language} editable={editable} timeframe={timeframe} depth={depth + 1} onChange={(next) => onChange?.({ ...node, steps: node.steps.map((item, position) => position === index ? next : item) })} />{index < node.steps.length - 1 && <div aria-label="then">↓</div>}</div>)}
    <label className="expression-parameter"><small>{zh ? "最大间隔（已确认 K 线）" : "Maximum gap (confirmed candles)"}</small><input disabled={!editable} type="number" min="1" max="500" value={node.max_gap_bars} onChange={(event) => onChange?.({ ...node, max_gap_bars: Number(event.target.value) })} /></label>
    <small>{zh ? "事件时间：最后一步确认时" : "Event time: final step confirmation"}</small></div>;
  const canAdd = editable && depth < EXPRESSION_MAX_DEPTH && node.children.length < 8 && expressionLeaves(node) < EXPRESSION_MAX_LEAVES;
  const canAddGroup = canAdd && depth + 1 < EXPRESSION_MAX_DEPTH;
  return <div className={`expression-group ${node.node_type.toLowerCase()}`} data-node={node.node_type}><header><strong>{node.node_type === "ALL" ? (zh ? "全部满足" : "ALL") : (zh ? "至少一项满足" : "ANY")}</strong>
    {editable && <select aria-label={zh ? "逻辑组" : "Logic group"} value={node.node_type} onChange={(event) => onChange?.({ ...node, node_type: event.target.value as "ALL" | "ANY" })}><option value="ALL">AND / ALL</option><option value="ANY">OR / ANY</option></select>}</header>
    {node.children.map((child, index) => <ExpressionTree key={index} node={child} capabilities={capabilities} language={language} editable={editable} timeframe={timeframe} depth={depth + 1}
      onChange={(next) => onChange?.({ ...node, children: node.children.map((item, position) => position === index ? next : item) })}
      onRemove={node.children.length > 2 ? () => onChange?.({ ...node, children: node.children.filter((_, position) => position !== index) }) : undefined} />)}
    {canAdd && <div className="expression-add-actions"><button type="button" className="secondary-btn compact" onClick={() => onChange?.({ ...node, children: [...node.children, defaultCondition(capabilities.features, timeframe)] })}><Plus size={14} />{zh ? "条件" : "Condition"}</button>
      {canAddGroup && <button type="button" className="secondary-btn compact" onClick={() => onChange?.({ ...node, children: [...node.children, { node_type: "ANY", children: [defaultCondition(capabilities.features, timeframe), defaultCondition(capabilities.features, timeframe)] }] })}><Plus size={14} />{zh ? "OR 组" : "OR group"}</button>}</div>}
  </div>;
}

export function AssumptionEditor({ assumptions, language, onChange }: { assumptions: ThesisPresetAssumption[]; language: Language; onChange: (next: ThesisPresetAssumption[]) => void }) {
  if (!assumptions.length) return null; const zh = language === "zh";
  return <section className="standardized-assumptions"><h3>{zh ? "标准化假设" : "Standardized assumptions"}</h3><p>{zh ? "这些版本化定义会写入研究定义哈希；运行前可修改。" : "These versioned definitions are recorded in the research hash and can be edited before running."}</p>
    {assumptions.map((assumption, index) => <div key={`${assumption.preset_id}-${index}`}><span><q>{assumption.source_text}</q> → {assumption.label[language]} <small>{assumption.preset_version}</small></span>
      <div>{typeof assumption.applied.value === "number" && <label>{zh ? "阈值" : "Threshold"}<input type="number" value={assumption.applied.value} onChange={(event) => onChange(assumptions.map((item, position) => position === index ? { ...item, applied: { ...item.applied, value: Number(event.target.value) } } : item))} /></label>}
      {Object.entries(assumption.applied.parameters).map(([name, value]) => <label key={name}>{name.replace(/_/g, " ")}<input type="number" value={String(value)} onChange={(event) => onChange(assumptions.map((item, position) => position === index ? { ...item, applied: { ...item.applied, parameters: { ...item.applied.parameters, [name]: Number(event.target.value) } } } : item))} /></label>)}</div></div>)}
  </section>;
}

const reasonCopy: Record<string, { en: string; zh: string }> = {
  SEMANTIC_UNSUPPORTED: { en: "This meaning does not yet have a reliable deterministic definition.", zh: "该语义目前还没有可靠的确定性定义。" },
  DATASET_UNAVAILABLE: { en: "No qualified historical dataset is currently available for this condition.", zh: "该条件目前没有合格的历史数据集。" },
  INSUFFICIENT_HISTORY: { en: "The available history is too short for this definition.", zh: "现有历史覆盖不足以检验该定义。" },
  NEEDS_PARAMETER: { en: "A parameter must be specified before this can be tested.", zh: "运行前需要补充一个明确参数。" },
  CURRENT_ONLY: { en: "This condition is available for current evidence only.", zh: "该条件目前仅支持当前证据。" },
  HISTORICAL_ONLY: { en: "This condition can be tested historically but cannot yet be tracked.", zh: "该条件可做历史检验，但目前不可跟踪。" },
  SOURCE_STALE: { en: "The latest source data is too stale for a qualified evaluation.", zh: "最新数据源已过期，无法进行合格评估。" },
  CAPABILITY_DISABLED: { en: "This capability is intentionally disabled until its data and semantics qualify.", zh: "该能力在数据和语义合格前保持关闭。" },
  CVD_HISTORICAL_NATIVE_SOURCE_UNAVAILABLE: { en: "Qualified native trade-side history for CVD is unavailable. CVD is not inferred from candles.", zh: "目前没有合格的原生逐笔主动买卖历史；系统不会用 K 线伪造 CVD。" },
  OVERLAPPING_FORWARD_WINDOW: { en: "Excluded by the independent-event overlap embargo.", zh: "该事件因前瞻窗口重叠而被独立事件规则排除。" },
  FORWARD_HORIZON_UNAVAILABLE: { en: "The complete forward horizon is not available at this historical cutoff.", zh: "在该历史截止点之后没有完整的前瞻周期。" },
  FUTURE_OUTCOME_CENSORED: { en: "The forward outcome is censored at the dataset cutoff.", zh: "前瞻结果在数据集截止点被删失。" },
};

const statusCopy: Record<string, { en: string; zh: string }> = {
  QUALIFIED: { en: "Qualified", zh: "数据合格" },
  COMPLETE: { en: "Complete", zh: "完整" },
  LIMITED: { en: "Limited", zh: "有限" },
  BLOCKED: { en: "Blocked", zh: "不可用" },
  AVAILABLE: { en: "Available", zh: "可用" },
  UNAVAILABLE: { en: "Unavailable", zh: "不可用" },
  INCLUDED: { en: "Included", zh: "已纳入" },
  EXCLUDED: { en: "Excluded", zh: "已排除" },
  SUFFICIENT_SPAN: { en: "Sufficient history", zh: "历史跨度充足" },
  LIMITED_HISTORICAL_SPAN: { en: "Limited history", zh: "历史跨度有限" },
};

export function friendlyStatus(value: string, language: Language) {
  return (statusCopy[value] || { en: "Policy status recorded", zh: "已记录策略状态" })[language];
}

export function friendlyReason(code: string, category: string | undefined, language: Language) {
  const inferred = /CVD/.test(code) ? "CVD_HISTORICAL_NATIVE_SOURCE_UNAVAILABLE"
    : /STALE/.test(code) ? "SOURCE_STALE"
    : /HISTOR|HISTORY|COVERAGE|WARMUP|GAP/.test(code) ? "INSUFFICIENT_HISTORY"
    : /CURRENT_ONLY/.test(code) ? "CURRENT_ONLY"
    : /HISTORICAL_ONLY/.test(code) ? "HISTORICAL_ONLY"
    : /UNAVAILABLE|DATASET|SOURCE/.test(code) ? "DATASET_UNAVAILABLE" : "";
  return (reasonCopy[code] || reasonCopy[category || ""] || reasonCopy[inferred] || {
    en: "This clause cannot be evaluated under the qualified data and semantic policy.",
    zh: "该条件未通过当前的数据与语义合格策略，无法执行。",
  })[language];
}

export function CapabilityBrowser({ capabilities, language }: { capabilities: ThesisCapabilities; language: Language }) {
  const zh = language === "zh"; const groups = new Map<string, ThesisFeatureCapability[]>();
  capabilities.features.forEach((feature) => groups.set(feature.source_group, [...(groups.get(feature.source_group) || []), feature]));
  return <details className="thesis-capability-browser"><summary>{zh ? "我可以测试什么？" : "What can I test?"}</summary>
    <div>{[...groups].map(([group, features]) => <section key={group}><h3>{group.replace(/_/g, " ")}</h3><ul>{features.map((feature) => <li key={feature.code}><span>{feature.label[language]}
      {!featureAvailable(feature, "historical") && <small>{friendlyReason(feature.historical_availability_reason || feature.availability_reason || "DATASET_UNAVAILABLE", "DATASET_UNAVAILABLE", language)}</small>}
      {featureAvailable(feature, "historical") && !featureAvailable(feature, "current") && <small>{friendlyReason("HISTORICAL_ONLY", "HISTORICAL_ONLY", language)}</small>}</span><small>{zh ? "历史" : "Historical"}: {featureAvailable(feature, "historical") ? "✓" : "—"} · {zh ? "当前" : "Current"}: {featureAvailable(feature, "current") ? "✓" : "—"}</small></li>)}</ul></section>)}</div>
  </details>;
}
