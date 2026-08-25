import { BrainCircuit, CheckCircle2, FlaskConical, Plus, ShieldAlert, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useLanguage } from "../i18n";
import { createTrackedThesis } from "../tracking/api";
import type { TrackMutation } from "../tracking/types";
import { explainThesis, fetchThesisCapabilities, fetchThesisEventContext, parseThesis, testThesis } from "./api";
import EvidenceCandlestickChart from "./EvidenceCandlestickChart";
import { AssumptionEditor, CapabilityBrowser, defaultCondition, ExpressionTree, expressionIsTrackable, expressionIsValid, featureAvailable, friendlyReason, friendlyStatus } from "./expressionV2";
import { thesisText } from "./i18n";
import { canRunDefinition, executableSpec, formatFraction, formatTimestamp, type EditableDefinition } from "./state";
import type { EvidenceExplanation, PartialThesisCondition, ThesisCapabilities, ThesisEventContext, ThesisEventRecord, ThesisExpressionV2, ThesisParseResult, ThesisParseResultV1, ThesisParseResultV2, ThesisSpecV2, ThesisTestResult } from "./types";

const examples = {
  en: [
    "BTC 4H volume ratio >= 1.2 and price above MA200. What happened over the next 4H, 12H and 24H historically?",
    "ETH 4H RSI >= 70. What happened over the next 4H, 12H and 24H historically?",
    "SOL 1H ATR percentage >= 2%. What happened over the next 4H, 12H and 24H historically?",
  ],
  zh: [
    "BTC 4H 成交量比率 >= 1.2，同时价格高于 MA200。之后 4H、12H、24H 历史上发生了什么？",
    "ETH 4H RSI >= 70。之后 4H、12H、24H 历史上发生了什么？",
    "SOL 1H ATR 百分比 >= 2%。之后 4H、12H、24H 历史上发生了什么？",
  ],
};

function capabilityExamples(capabilities: ThesisCapabilities | null, language: "en" | "zh") {
  if (!capabilities || capabilities.thesis_spec_version === "thesis-spec-v1") return examples[language];
  const instrument = capabilities.instruments[0] || "BTC"; const timeframe = capabilities.timeframes.includes("4H") ? "4H" : capabilities.timeframes[0];
  const horizon = capabilities.horizons.includes("24H") ? "24H" : capabilities.horizons[0];
  const presets = capabilities.semantic_presets?.presets.filter((preset) => capabilities.features.some((feature) => feature.code === preset.feature && featureAvailable(feature, "historical"))) || [];
  const phrases = presets.slice(0, 3).map((preset) => `${instrument} ${timeframe} ${preset.label[language]}${language === "zh" ? `，之后 ${horizon} 历史上怎么样？` : `. What happened over the next ${horizon} historically?`}`);
  if (phrases.length) return phrases;
  return capabilities.features.filter((feature) => feature.value_type === "boolean" && featureAvailable(feature, "historical")).slice(0, 3)
    .map((feature) => `${instrument} ${timeframe} ${feature.label[language]} = true${language === "zh" ? `，之后 ${horizon} 历史上怎么样？` : `. What happened over the next ${horizon} historically?`}`);
}

const emptyDefinition: EditableDefinition = { instrument: "", timeframe: "", required: [], optional: [], horizons: ["4H", "12H", "24H"] };

function sampleExplanation(quality: string, language: "en" | "zh") {
  const values = language === "zh" ? {
    INSUFFICIENT: "独立事件过少，估计不稳定。", LOW: "历史样本有限。",
    MODERATE: "历史证据具有参考价值，但仍存在不确定性。", ADEQUATE: "历史样本较大，但仍不保证未来结果。",
  } : {
    INSUFFICIENT: "Too few independent events for a stable estimate.", LOW: "Historical sample is limited.",
    MODERATE: "Useful historical evidence, but uncertainty remains.", ADEQUATE: "Larger historical sample; still not a future guarantee.",
  };
  return values[quality as keyof typeof values] || quality;
}

function isV2Parse(value: ThesisParseResult): value is ThesisParseResultV2 { return value.version === "thesis-parse-result-v2"; }

function definitionFromParse(value: ThesisParseResultV1): EditableDefinition {
  const partial = value.partial_spec;
  if (!partial) return emptyDefinition;
  return {
    instrument: partial.instrument || "", timeframe: partial.timeframe || "",
    required: partial.required_conditions.map((condition) => ({ ...condition })),
    optional: partial.optional_conditions.map((condition) => ({ ...condition })),
    horizons: [...partial.forward_horizons],
  };
}

function applyAssumption(expression: ThesisExpressionV2, assumption: ThesisSpecV2["assumptions"][number]): ThesisExpressionV2 {
  if (expression.node_type === "CONDITION") return expression.feature === assumption.feature
    ? { ...expression, operator: assumption.applied.operator, value: assumption.applied.value, parameters: assumption.applied.parameters }
    : expression;
  if (expression.node_type === "NOT") return { ...expression, child: applyAssumption(expression.child, assumption) };
  return { ...expression, children: expression.children.map((child) => applyAssumption(child, assumption)) };
}

export default function TestAnIdeaPage() {
  const { language } = useLanguage();
  const labels = thesisText(language);
  const [capabilities, setCapabilities] = useState<ThesisCapabilities | null>(null);
  const [capabilityError, setCapabilityError] = useState(false);
  const [text, setText] = useState("");
  const [parseResult, setParseResult] = useState<ThesisParseResult | null>(null);
  const [definition, setDefinition] = useState<EditableDefinition>(emptyDefinition);
  const [v2Spec, setV2Spec] = useState<ThesisSpecV2 | null>(null);
  const [result, setResult] = useState<ThesisTestResult | null>(null);
  const [phase, setPhase] = useState<"idle" | "parsing" | "testing">("idle");
  const [error, setError] = useState("");
  const [manual, setManual] = useState(false);
  const [removed, setRemoved] = useState(false);
  const sequence = useRef(0);
  const [selectedEvent, setSelectedEvent] = useState<ThesisEventRecord | null>(null);
  const [eventContext, setEventContext] = useState<ThesisEventContext | null>(null);
  const [eventContextState, setEventContextState] = useState<"idle" | "loading" | "error">("idle");
  const eventContextController = useRef<AbortController | null>(null);
  const eventContextSequence = useRef(0);
  const [explanation, setExplanation] = useState<EvidenceExplanation | null>(null);
  const [explanationLoading, setExplanationLoading] = useState(false);
  const explanationSequence = useRef(0);
  const [tracked, setTracked] = useState<TrackMutation | null>(null);
  const [trackError, setTrackError] = useState(false);
  const [trackPending, setTrackPending] = useState(false);
  const trackPendingRef = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    fetchThesisCapabilities(controller.signal).then(setCapabilities).catch((reason) => {
      if ((reason as Error).name !== "AbortError") setCapabilityError(true);
    });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!result) { ++explanationSequence.current; setExplanation(null); setExplanationLoading(false); return; }
    const controller = new AbortController();
    const current = ++explanationSequence.current;
    const currentHash = result.result_hash;
    setExplanation(null); setExplanationLoading(true);
    explainThesis(result, language, controller.signal).then((value) => {
      if (current === explanationSequence.current && value.result_hash === currentHash) setExplanation(value);
    }).catch((reason) => {
      if ((reason as Error).name !== "AbortError") setExplanation(null);
    }).finally(() => { if (!controller.signal.aborted && current === explanationSequence.current) setExplanationLoading(false); });
    return () => controller.abort();
  }, [result, language]);

  useEffect(() => () => eventContextController.current?.abort(), []);

  const availableFeatures = useMemo(() => capabilities?.features.filter((item) => featureAvailable(item, "historical")) || [], [capabilities]);
  const v2Blocked = parseResult?.status === "UNSUPPORTED" || parseResult?.status === "PARTIALLY_SUPPORTED" || parseResult?.status === "NEEDS_INPUT";
  const resultTrackable = !!result && !!capabilities && (result.thesis_spec.version !== "thesis-spec-v2"
    || expressionIsTrackable(result.thesis_spec.expression, capabilities));
  const runnable = phase === "idle" && (v2Spec && capabilities
    ? !v2Blocked && capabilities.instruments.includes(v2Spec.instrument) && capabilities.timeframes.includes(v2Spec.timeframe)
      && v2Spec.forward_horizons.length > 0 && expressionIsValid(v2Spec.expression, capabilities, v2Spec.timeframe)
    : canRunDefinition(definition, capabilities) && parseResult?.status !== "UNSUPPORTED");

  async function interpret() {
    if (!text.trim() || phase !== "idle") return;
    const current = ++sequence.current;
    setPhase("parsing"); setError(""); setResult(null); setTracked(null); setTrackError(false); setRemoved(false); setManual(false);
    const controller = new AbortController();
    try {
      const parsed = await parseThesis({ version: capabilities?.thesis_spec_version === "thesis-spec-v2" ? "thesis-parse-request-v2" : "thesis-parse-request-v1", text: text.trim(), language }, controller.signal);
      if (current !== sequence.current) return;
      setParseResult(parsed);
      if (isV2Parse(parsed)) setV2Spec(parsed.thesis_spec);
      else { setV2Spec(null); setDefinition(definitionFromParse(parsed)); }
      if (parsed.status === "ERROR") setManual(true);
    } catch {
      if (current !== sequence.current) return;
      setError(labels.parseError); setManual(true); setParseResult(null);
    } finally {
      if (current === sequence.current) setPhase("idle");
    }
  }

  function startManual() {
    setManual(true); setParseResult(null); setResult(null); setTracked(null); setTrackError(false); setError("");
    if (capabilities?.thesis_spec_version === "thesis-spec-v2") {
      const timeframe = capabilities.timeframes[0] || "4H";
      setV2Spec({ version: "thesis-spec-v2", instrument: capabilities.instruments[0] || "", timeframe,
        expression: defaultCondition(capabilities.features, timeframe), forward_horizons: capabilities.horizons.slice(0, 3),
        requested_as_of: Math.floor(Date.now() / 1000), assumptions: [], metadata: { source: "test-an-idea-manual-v2" } });
      return;
    }
    setV2Spec(null); setDefinition((current) => ({ ...current,
      instrument: current.instrument || capabilities?.instruments[0] || "",
      timeframe: current.timeframe || capabilities?.timeframes[0] || "",
      horizons: current.horizons.length ? current.horizons : [...(capabilities?.horizons || [])],
    }));
  }

  function addCondition() {
    if (!availableFeatures.length || definition.required.length + definition.optional.length >= 5) return;
    const feature = availableFeatures.find((item) => item.value_type === "number") || availableFeatures[0];
    setDefinition((current) => ({ ...current, required: [...current.required, {
      feature: feature.code, operator: feature.operators[0] || null,
      value: feature.value_type === "boolean" ? true : null,
    }] }));
  }

  function updateCondition(group: "required" | "optional", index: number, next: Partial<PartialThesisCondition>) {
    setDefinition((current) => ({ ...current, [group]: current[group].map((item, position) => position === index ? { ...item, ...next } : item) }));
  }

  async function runTest() {
    if (!capabilities || !runnable) return;
    eventContextController.current?.abort();
    setSelectedEvent(null); setEventContext(null); setEventContextState("idle");
    setPhase("testing"); setError(""); setResult(null); setTracked(null); setTrackError(false);
    try {
      const submitted = v2Spec ? { ...v2Spec, requested_as_of: Math.floor(Date.now() / 1000) } : executableSpec(definition, capabilities);
      const tested = await testThesis(submitted);
      if (tested.thesis_spec.instrument !== submitted.instrument || tested.thesis_spec.timeframe !== submitted.timeframe
          || tested.thesis_spec.version !== submitted.version
          || JSON.stringify(tested.thesis_spec) !== JSON.stringify(submitted)) {
        throw new Error("THESIS_RESULT_DEFINITION_MISMATCH");
      }
      setResult(tested);
    } catch { setError(labels.apiError); }
    finally { setPhase("idle"); }
  }

  async function viewEvent(event: ThesisEventRecord) {
    if (!result || event.exclusion_status !== "INCLUDED") return;
    eventContextController.current?.abort();
    const controller = new AbortController();
    const current = ++eventContextSequence.current;
    eventContextController.current = controller;
    setSelectedEvent(event); setEventContext(null); setEventContextState("loading");
    try {
      const value = await fetchThesisEventContext(result, event, controller.signal);
      if (current === eventContextSequence.current && value.event.event_id === event.event_id) {
        setEventContext(value); setEventContextState("idle");
      }
    } catch (reason) {
      if ((reason as Error).name !== "AbortError" && current === eventContextSequence.current) setEventContextState("error");
    }
  }

  async function trackThesis() {
    if (!result || !resultTrackable || result.status !== "COMPLETED" || !result.coverage.testable || trackPendingRef.current) return;
    trackPendingRef.current = true; setTrackPending(true); setTrackError(false);
    try {
      setTracked(await createTrackedThesis({ result_hash: result.result_hash, thesis_spec: result.thesis_spec,
        language, ...(text.trim() ? { original_text: text.trim() } : {}) }));
    } catch { setTrackError(true); }
    finally { trackPendingRef.current = false; setTrackPending(false); }
  }

  const topQuality = result?.aggregates["24H"]?.sample_quality || result && Object.values(result.aggregates)[0]?.sample_quality;
  const recentEvents = result?.event_records.slice(-20).reverse() || [];

  return <main className="thesis-page">
    <section className="thesis-hero">
      <div className="thesis-eyebrow"><FlaskConical size={15} /> AI interprets. The deterministic engine measures.</div>
      <h1>{labels.title}</h1><p>{labels.subtitle}</p>
      <textarea aria-label={labels.title} value={text} disabled={phase === "parsing"} onChange={(event) => setText(event.target.value)} placeholder={labels.placeholder} maxLength={2000} />
      <div className="thesis-input-actions">
        <button className="primary-btn" disabled={!text.trim() || phase !== "idle" || !capabilities} onClick={interpret}>
          <BrainCircuit size={17} /> {phase === "parsing" ? labels.interpreting : labels.interpret}
        </button>
        <button className="secondary-btn" disabled={!capabilities || phase !== "idle"} onClick={startManual}>{labels.manual}</button>
      </div>
      <div className="thesis-examples"><small>{labels.examples}</small>
        {capabilityExamples(capabilities, language).map((example) => <button key={example} disabled={phase === "parsing"} onClick={() => setText(example)}>{example}</button>)}
      </div>
      {capabilities && <CapabilityBrowser capabilities={capabilities} language={language} />}
      {capabilityError && <div className="thesis-alert error" role="alert">{labels.apiError}</div>}
    </section>

    {phase === "parsing" && <section className="thesis-card thesis-loading" role="status"><span className="thesis-spinner" />{labels.interpreting}</section>}
    {error && <section className="thesis-alert error" role="alert">{error} <button onClick={() => setError("")}>{labels.retry}</button></section>}

    {(parseResult?.status === "UNSUPPORTED" || parseResult?.status === "PARTIALLY_SUPPORTED") && <section className="thesis-card thesis-unsupported" data-state="unsupported">
      <ShieldAlert size={23} /><div><h2>{labels.unsupported}</h2><h3>{labels.unsupportedList}</h3>
      <ul>{parseResult.unsupported_clauses.map((item, index) => <li key={index}><strong>{item.source_text}</strong><span>{friendlyReason(item.reason_code, "category" in item ? item.category : undefined, language)}</span>{"suggestions" in item && item.suggestions?.length ? <small>{language === "zh" ? "可选择的相关条件：" : "Related testable conditions: "}{item.suggestions.join(" · ")}</small> : null}</li>)}</ul>
      <p>{labels.noResult}</p></div>
    </section>}
    {parseResult?.status === "NEEDS_INPUT" && !v2Spec && <section className="thesis-card thesis-unsupported" data-state="needs-input"><ShieldAlert size={23} /><div><h2>{labels.needs}</h2>
      <ul>{parseResult.missing_parameters.map((item, index) => <li key={index}><strong>{"source_text" in item ? item.source_text : item.feature}</strong><span>{friendlyReason("NEEDS_PARAMETER", "NEEDS_PARAMETER", language)} ({"parameter" in item ? item.parameter : item.field})</span></li>)}</ul></div></section>}

    {v2Spec && capabilities && <section className="thesis-card thesis-definition thesis-definition-v2" data-state={parseResult?.status || "MANUAL_V2"}>
      <div className="thesis-card-heading"><div><span className="thesis-eyebrow">{labels.definition} · V2</span><h2>{parseResult ? labels.understood : labels.manual}</h2></div>
        {(parseResult?.status === "READY" || parseResult?.status === "READY_WITH_ASSUMPTIONS") && <CheckCircle2 className="positive" />}</div>
      {parseResult?.status === "NEEDS_INPUT" && <div className="thesis-alert warning">{labels.needs}</div>}
      <div className="thesis-definition-meta"><label>Instrument<select value={v2Spec.instrument} onChange={(event) => setV2Spec({ ...v2Spec, instrument: event.target.value })}>{capabilities.instruments.map((item) => <option key={item}>{item}</option>)}</select></label>
        <label>Timeframe<select value={v2Spec.timeframe} onChange={(event) => setV2Spec({ ...v2Spec, timeframe: event.target.value })}>{capabilities.timeframes.map((item) => <option key={item}>{item}</option>)}</select></label></div>
      <ExpressionTree node={v2Spec.expression} capabilities={capabilities} language={language} timeframe={v2Spec.timeframe} editable onChange={(expression) => setV2Spec({ ...v2Spec, expression })} />
      <div className="expression-root-actions"><button className="secondary-btn compact" disabled={v2Spec.expression.node_type !== "CONDITION"} onClick={() => setV2Spec({ ...v2Spec, expression: { node_type: "ALL", children: [v2Spec.expression, defaultCondition(capabilities.features, v2Spec.timeframe)] } })}><Plus size={14} />AND</button>
        <button className="secondary-btn compact" disabled={v2Spec.expression.node_type !== "CONDITION"} onClick={() => setV2Spec({ ...v2Spec, expression: { node_type: "ANY", children: [v2Spec.expression, defaultCondition(capabilities.features, v2Spec.timeframe)] } })}><Plus size={14} />OR</button></div>
      <AssumptionEditor assumptions={v2Spec.assumptions} language={language} onChange={(assumptions) => {
        let expression = v2Spec.expression;
        const unchanged = assumptions.filter((assumption, index) => {
          const edited = JSON.stringify(assumption) !== JSON.stringify(v2Spec.assumptions[index]);
          if (edited) expression = applyAssumption(expression, assumption);
          return !edited;
        });
        // An edited preset becomes an explicit user value. Keeping the old preset
        // assertion would be false and the backend correctly rejects altered presets.
        setV2Spec({ ...v2Spec, assumptions: unchanged, expression });
      }} />
      <h3>{labels.outcomes}</h3><div className="thesis-horizons">{capabilities.horizons.map((horizon) => <label key={horizon}><input type="checkbox" checked={v2Spec.forward_horizons.includes(horizon)} onChange={(event) => setV2Spec({ ...v2Spec, forward_horizons: event.target.checked ? [...v2Spec.forward_horizons, horizon] : v2Spec.forward_horizons.filter((item) => item !== horizon) })} />{horizon}</label>)}</div>
      <button className="primary-btn thesis-run" disabled={!runnable} onClick={runTest}>{phase === "testing" ? labels.testing : labels.run}</button>
    </section>}

    {!v2Spec && (parseResult && !isV2Parse(parseResult) && parseResult.status !== "UNSUPPORTED" || manual && capabilities?.thesis_spec_version !== "thesis-spec-v2") && capabilities && <section className="thesis-card thesis-definition" data-state={parseResult?.status || "MANUAL"}>
      <div className="thesis-card-heading"><div><span className="thesis-eyebrow">{labels.definition}</span><h2>{parseResult ? labels.understood : labels.manual}</h2></div>
        {parseResult?.status === "READY" && <CheckCircle2 className="positive" />}</div>
      {parseResult?.status === "ERROR" && <div className="thesis-alert warning">{labels.aiUnavailable}</div>}
      {parseResult?.status === "NEEDS_INPUT" && <div className="thesis-alert warning">{labels.needs}</div>}
      <fieldset className="thesis-definition-fields" disabled={phase === "testing"}><div className="thesis-definition-meta">
        <label>Instrument<select aria-label={language === "zh" ? "交易标的" : "Instrument"} value={definition.instrument} onChange={(event) => setDefinition({ ...definition, instrument: event.target.value })}>
          <option value="">—</option>{capabilities.instruments.map((item) => <option key={item}>{item}</option>)}</select></label>
        <label>Timeframe<select aria-label={language === "zh" ? "时间周期" : "Timeframe"} value={definition.timeframe} onChange={(event) => setDefinition({ ...definition, timeframe: event.target.value })}>
          <option value="">—</option>{capabilities.timeframes.map((item) => <option key={item}>{item}</option>)}</select></label>
      </div>
      <h3>{labels.conditions}</h3>
      <div className="thesis-condition-list">
        {[...definition.required.map((condition) => ({ condition, group: "required" as const })),
          ...definition.optional.map((condition) => ({ condition, group: "optional" as const }))].map(({ condition, group }, index) => {
          const groupIndex = group === "required" ? index : index - definition.required.length;
          const feature = capabilities.features.find((item) => item.code === condition.feature);
          return <div className="thesis-condition" data-requirement={group.toUpperCase()} key={`${group}-${condition.feature}-${groupIndex}`}>
            {manual ? <select aria-label={`Feature ${index + 1}`} value={condition.feature} onChange={(event) => {
              const next = availableFeatures.find((item) => item.code === event.target.value)!;
              updateCondition(group, groupIndex, { feature: next.code, operator: next.operators[0], value: next.value_type === "boolean" ? true : null });
            }}>{availableFeatures.map((item) => <option key={item.code} value={item.code}>{item.label[language]}</option>)}</select>
              : <div className="thesis-feature"><strong>{feature?.label[language] || condition.feature}</strong><code>{condition.feature} · {group.toUpperCase()}</code></div>}
            <select aria-label={`Operator ${index + 1}`} value={condition.operator || ""} onChange={(event) => updateCondition(group, groupIndex, { operator: event.target.value })}>
              <option value="">—</option>{feature?.operators.map((item) => <option key={item} value={item}>{item}</option>)}</select>
            {feature?.value_type === "boolean" ? <span className="thesis-fixed-value">{labels.true}</span> : <label className="thesis-value">
              <input aria-label={`Threshold ${index + 1}`} type="number" step="any" min={feature?.bounds.minimum ?? undefined} max={feature?.bounds.maximum ?? undefined}
                value={typeof condition.value === "number" ? condition.value : ""} placeholder={labels.threshold}
                onChange={(event) => updateCondition(group, groupIndex, { value: event.target.value === "" ? null : Number(event.target.value) })} />
              <small>{feature?.unit}</small></label>}
            <button className="icon-button" aria-label={`${labels.remove} ${index + 1}`} onClick={() => { setDefinition({ ...definition, [group]: definition[group].filter((_, position) => position !== groupIndex) }); setRemoved(true); }}><Trash2 size={16} /></button>
          </div>;
        })}
      </div>
      {removed && <p className="thesis-user-change">{labels.removed}</p>}
      {manual && <button className="secondary-btn thesis-add" disabled={definition.required.length + definition.optional.length >= 5} onClick={addCondition}><Plus size={16} />{labels.add}</button>}
      <h3>{labels.outcomes}</h3><div className="thesis-horizons">
        {capabilities.horizons.map((horizon) => <label key={horizon}><input type="checkbox" checked={definition.horizons.includes(horizon)} onChange={(event) => setDefinition({ ...definition, horizons: event.target.checked ? [...definition.horizons, horizon] : definition.horizons.filter((item) => item !== horizon) })} />{horizon}</label>)}
      </div></fieldset>
      <button className="primary-btn thesis-run" disabled={!runnable} onClick={runTest}>{phase === "testing" ? labels.testing : labels.run}</button>
    </section>}

    {phase === "testing" && <section className="thesis-card thesis-loading" role="status"><span className="thesis-spinner" />{labels.testing}</section>}

    {result && <section className="thesis-results" aria-label={labels.evidence}>
      <div className="thesis-result-hero thesis-card">
        <div><span className="thesis-eyebrow">{result.instrument} · {result.timeframe}</span><h2>{labels.evidence}</h2><p>{labels.historicalFrame}</p></div>
        <div className="thesis-result-metrics"><div><strong>{result.independent_event_count.toLocaleString()}</strong><span>{labels.independent}</span></div>
          <div><strong>{topQuality || "—"}</strong><span>{labels.sample}</span></div></div>
        <p>{labels.tested}: {formatTimestamp(result.tested_range.start, language)} → {formatTimestamp(result.tested_range.end, language)}</p>
        {!result.coverage.testable && <div className="thesis-alert error" role="alert">{labels.notTestable}{result.coverage.reason ? ` ${friendlyReason(result.coverage.reason, "INSUFFICIENT_HISTORY", language)}` : ""}</div>}
        {result.independent_event_count === 0 && result.status === "COMPLETED" && <div className="thesis-alert warning">{labels.noMatches}</div>}
        {topQuality && <div className="thesis-sample-note"><strong>{topQuality}</strong> · {sampleExplanation(topQuality, language)}</div>}
        <div className="thesis-track-action">
          <button className="primary-btn" disabled={trackPending || !!tracked || !resultTrackable || result.status !== "COMPLETED" || !result.coverage.testable} onClick={trackThesis}>{trackPending ? labels.tracking : tracked ? labels.trackingStarted : labels.track}</button>
          {!resultTrackable && <span role="status">{friendlyReason("HISTORICAL_ONLY", "HISTORICAL_ONLY", language)}</span>}
          {tracked && <a href={`/tracking/${encodeURIComponent(tracked.track.track_id)}`}>{labels.viewTracking}</a>}
          {trackError && <span role="alert">{labels.trackError}</span>}
        </div>
      </div>

      <section className="thesis-card"><h2>{labels.evidence}</h2><div className="thesis-table-scroll"><table className="thesis-table"><thead><tr>
        <th>{labels.horizon}</th><th>N</th><th>{labels.positive}</th><th>{labels.median}</th><th>{labels.p25}</th><th>{labels.p75}</th><th>{labels.mfe}</th><th>{labels.mae}</th><th>{labels.sample}</th>
      </tr></thead><tbody>{result.thesis_spec.forward_horizons.map((horizon) => { const aggregate = result.aggregates[horizon]; return aggregate ? <tr key={horizon}>
        <td><strong>{horizon}</strong><small>{aggregate.eligible_n} {labels.usable} · {aggregate.censored_n} {labels.censored}</small></td>
        <td>{aggregate.eligible_n}</td><td>{formatFraction(aggregate.historical_positive_rate, language)}</td><td>{formatFraction(aggregate.median_return_fraction, language)}</td>
        <td>{formatFraction(aggregate.p25_return_fraction, language)}</td><td>{formatFraction(aggregate.p75_return_fraction, language)}</td>
        <td>{formatFraction(aggregate.median_mfe_fraction, language)}</td><td>{formatFraction(aggregate.median_mae_fraction, language)}</td><td>{aggregate.sample_quality}</td>
      </tr> : null; })}</tbody></table></div></section>

      <section className="thesis-card thesis-explanation" aria-live="polite"><h2>{labels.whatEvidenceSays}</h2>
        {explanationLoading && <p className="thesis-muted">{labels.explanationLoading}</p>}
        {explanation ? <>{explanation.blocks.map((block) => <p key={block.template_id}>{block.text}</p>)}
          {explanation.status === "FALLBACK" && <small>{labels.deterministicFallback}</small>}</>
          : !explanationLoading && <p>{labels.explanationUnavailable}</p>}
      </section>

      <div className="thesis-result-grid"><section className="thesis-card"><h2>{labels.coverage}</h2>
        <div className={`thesis-coverage-status ${result.coverage.testable ? "ok" : "blocked"}`}><strong>{friendlyStatus(result.coverage.qualification, language)}</strong><span>{result.coverage.testable ? "✓" : "—"}</span></div>
        {result.coverage.reason && <p>{friendlyReason(result.coverage.reason, undefined, language)}</p>}
        <dl className="thesis-history-lineage"><div><dt>{labels.source}</dt><dd>{result.historical_data.source_label}</dd></div>
          <div><dt>{labels.rawHistory}</dt><dd>{formatTimestamp(result.historical_data.raw_range.start, language)} → {formatTimestamp(result.historical_data.raw_range.end, language)}</dd></div>
          <div><dt>{labels.evaluableHistory}</dt><dd>{formatTimestamp(result.historical_data.evaluable_range.start, language)} → {formatTimestamp(result.historical_data.evaluable_range.end, language)}</dd></div>
          <div><dt>{labels.reductionReason}</dt><dd>{result.historical_data.reduction_reasons.length ? result.historical_data.reduction_reasons.map((item) => friendlyReason(item, undefined, language)).join(" · ") : labels.none}</dd></div></dl>
        {result.historical_data.breadth_qualification === "LIMITED_HISTORICAL_SPAN" && <div className="thesis-alert warning"><strong>{labels.limitedSpan}</strong><br />{labels.limitedSpanBody.replace("{days}", String(result.historical_data.span_days || 0))}</div>}
        <ul className="thesis-coverage-list">{result.coverage.features.map((item) => <li key={item.feature}><div><strong>{capabilities?.features.find((feature) => feature.code === item.feature)?.label[language] || item.feature}</strong><small>{friendlyReason(item.reason, undefined, language)}</small></div><span>{friendlyStatus(item.qualification, language)}</span></li>)}</ul>
      </section><section className="thesis-card"><h2>{labels.limitations}</h2><ul>{result.limitations.map((item) => <li key={item}>{friendlyReason(item, undefined, language)}</li>)}{result.warnings.map((item) => <li key={item}>{friendlyReason(item, undefined, language)}</li>)}</ul></section></div>

      <section className="thesis-card"><h2>{labels.matches}</h2><p>{labels.showing.replace("{shown}", String(recentEvents.length)).replace("{total}", String(result.event_records.length))}</p>
        <div className="thesis-events">{recentEvents.map((event) => <article key={event.event_id} className={event.exclusion_status === "EXCLUDED" ? "excluded" : ""}>
          <header><time>{formatTimestamp(event.timestamp, language)}</time><span>{friendlyStatus(event.exclusion_status, language)}</span></header><strong>{labels.reference}: {event.reference_close.toLocaleString()}</strong>
          {event.exclusion_status === "EXCLUDED" ? <div><span>{friendlyReason(event.exclusion_reason || "OVERLAPPING_FORWARD_WINDOW", undefined, language)}</span></div>
            : <div>{result.thesis_spec.forward_horizons.map((horizon) => { const outcome = event.outcomes[horizon]; return <span key={horizon}><b>{horizon}</b> {outcome?.available
              ? <>{formatFraction(outcome.forward_return_fraction, language)} · MFE {formatFraction(outcome.mfe_fraction, language)} · MAE {formatFraction(outcome.mae_fraction, language)}</>
              : `${labels.censored}: ${friendlyReason(outcome?.censor_reason || "FUTURE_OUTCOME_CENSORED", undefined, language)}`}</span>; })}</div>}
          {event.exclusion_status === "INCLUDED" && <button className="secondary-btn thesis-view-event" onClick={() => void viewEvent(event)}>{labels.viewEvent}</button>}
        </article>)}</div>
        {selectedEvent && <section className="thesis-selected-event" aria-label={labels.eventEvidence}>
          <header><div><span className="thesis-eyebrow">{labels.historicalEvent}</span><h3>{formatTimestamp(selectedEvent.timestamp, language)}</h3></div>
            <button className="icon-button" aria-label={labels.close} onClick={() => { eventContextController.current?.abort(); setSelectedEvent(null); setEventContext(null); setEventContextState("idle"); }}>×</button></header>
          {eventContextState === "loading" && <div className="thesis-loading" role="status"><span className="thesis-spinner" />{labels.loadingEvent}</div>}
          {eventContextState === "error" && <div className="thesis-alert error">{labels.eventError}</div>}
          {eventContext && <div className="thesis-event-evidence-grid"><div className="thesis-event-facts">
            <p><span>{labels.reference}</span><strong>{eventContext.event.reference_close.toLocaleString()}</strong></p>
            <h4>{labels.conditions}</h4>{(eventContext.event.conditions || []).map((condition) => <p key={condition.feature}><span>{capabilities?.features.find((item) => item.code === condition.feature)?.label[language] || condition.feature}</span><strong>{labels.actual} {String(condition.actual)} · {condition.operator} {String(condition.expected)}</strong></p>)}
            {eventContext.event.structure_context?.map((structure) => <p key={`${structure.feature}-${structure.event_timestamp}`}><span>{capabilities?.features.find((item) => item.code === structure.feature)?.label[language] || structure.feature}</span><strong>{labels.reference} {structure.reference_level.toLocaleString()}</strong></p>)}
            {eventContext.horizons.map((horizon) => <p key={horizon.horizon}><span>{horizon.horizon}</span><strong>{horizon.available
              ? <>{formatFraction(horizon.forward_return_fraction, language)}<small>{labels.outcomeClose} {horizon.outcome_close?.toLocaleString()} · MFE {formatFraction(horizon.mfe_fraction, language)} · MAE {formatFraction(horizon.mae_fraction, language)}</small></>
              : friendlyReason(horizon.censor_reason || "FUTURE_OUTCOME_CENSORED", undefined, language)}</strong></p>)}
          </div><EvidenceCandlestickChart context={eventContext} accessibleLabel={labels.eventChart} /></div>}
        </section>}
      </section>

      <details className="thesis-card thesis-audit"><summary>{labels.evidenceDetails}</summary><dl>
        <div><dt>{labels.resultId}</dt><dd><code>{result.result_hash.slice(0, 12)}…</code></dd></div>
        <div><dt>{labels.definitionHash}</dt><dd><code>{result.definition_hash.slice(0, 12)}…</code></dd></div>
        <div><dt>{labels.dataset}</dt><dd><code>{result.historical_data.dataset_id.slice(0, 12)}…</code></dd></div>
        <div><dt>{labels.sourceVersion}</dt><dd>{result.historical_data.source_version || result.historical_data.immutable_store_verification || "—"}</dd></div>
        <div><dt>{labels.selectionPolicy}</dt><dd>{result.historical_data.selection_policy_version || "—"}</dd></div>
        <div><dt>{labels.engine}</dt><dd>{result.engine_version}</dd></div>
        <div><dt>{labels.coveragePolicy}</dt><dd>{result.coverage.version}</dd></div>
        <div><dt>{labels.independencePolicy}</dt><dd>{result.compiled_definition.independence_policy.version}</dd></div>
        <div><dt>{labels.featureVersions}</dt><dd>{Object.entries(result.feature_versions).map(([feature, version]) => `${feature}: ${version}`).join(" · ")}</dd></div>
      </dl></details>
    </section>}
  </main>;
}
