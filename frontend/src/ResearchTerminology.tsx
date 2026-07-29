import { useLanguage } from "./i18n";

const TERMS = [
  ["Natural coverage days / 自然覆盖天数", "Elapsed calendar days between the first and last confirmed source observations; gaps are not removed. / 首尾已确认源观测之间的自然日跨度，不扣除缺口。"],
  ["Gap-adjusted usable days / 缺口调整后可用天数", "Observed usable intervals after known gaps are removed; it is not the same as elapsed coverage. / 扣除已知缺口后的可用区间，不等于自然覆盖跨度。"],
  ["Maximum contiguous interval / 最大连续区间", "Longest uninterrupted usable interval. Short fragments are not added to inflate this value. / 最长不间断可用区间；不会把零散片段相加来放大。"],
  ["Label overlap / 标签重叠", "Time where both a feature and its forward label are present. It can be shorter than source coverage. / 特征与前瞻标签同时存在的时间，可能短于源覆盖。"],
  ["Native independent events / 原生独立事件数", "Distinct source-native events before horizon expansion. This is the independent-event count. / 跨 horizon 展开前的不同源事件，只有该口径可称为独立事件数。"],
  ["Non-overlapping labels / Non-overlapping label 数", "Labels selected so their forward outcome windows do not overlap. / 前瞻结果窗口互不重叠的标签数。"],
  ["Calibration / validation events / 校准与验证事件数", "Events assigned to chronological calibration and later validation partitions; the partitions are reported separately. / 按时间顺序分配到校准段和后续验证段的事件，分别报告。"],
  ["Cross-horizon cumulative count / 跨 horizon 累计数", "The sum of event rows evaluated across horizons. One native event may appear more than once, so this is never described as independent samples. / 各 horizon 事件行之和；同一原生事件可重复出现，因此绝不表述为独立样本。"],
] as const;

export default function ResearchTerminology({ compact = false }: { compact?: boolean }) {
  const { language } = useLanguage();
  const zh = language === "zh";
  return (
    <details className={`research-terminology ${compact ? "compact" : ""}`}>
      <summary>{zh ? "研究口径说明（中英双语）" : "Research measure guide (中英双语)"}</summary>
      <p className="terminology-warning">
        {zh
          ? "以下说明只解释展示口径，不改变 API 数值，也不构成交易建议。"
          : "These definitions explain displayed API values only; they do not alter values and are not trading advice."}
      </p>
      <dl>
        {TERMS.map(([term, definition]) => <div key={term} title={definition}><dt>{term}</dt><dd>{definition}</dd></div>)}
      </dl>
      <div className="readiness-guide">
        <p><b>EXPLORATORY_ONLY</b> · {zh ? "仅允许描述性探索；覆盖、独立事件或时间验证证据仍不足。" : "Descriptive exploration only; coverage, independent events, or temporal validation evidence is insufficient."}</p>
        <p><b>VALIDATION_READY</b> · {zh ? "已具备运行预先定义验证的最低证据，但尚不能形成正式研究结论。" : "Minimum evidence exists for pre-specified validation, but not for a formal research conclusion."}</p>
        <p><b>FORMAL_RESEARCH_READY</b> · {zh ? "覆盖、非重叠标签和校准/后续验证门槛均满足；仍只表示研究就绪。" : "Coverage, non-overlapping labels, and calibration/later-validation gates pass; this means research-ready only."}</p>
      </div>
    </details>
  );
}
