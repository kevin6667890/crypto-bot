import type { UiLanguage } from "./enumTranslations";

export type SemanticKind = "FACT" | "DETERMINISTIC_DERIVATION" | "AI_SYNTHESIS" | "UNCERTAINTY" | "COUNTEREVIDENCE" | "MISSING_DATA";
const semantic: Record<SemanticKind, { glyph:string; zh:string; en:string; zhDescription:string; enDescription:string }> = {
  FACT:{glyph:"●",zh:"数据事实",en:"Data fact",zhDescription:"来自冻结证据的事实",enDescription:"Fact from frozen evidence"},
  DETERMINISTIC_DERIVATION:{glyph:"◆",zh:"程序推导",en:"Deterministic derivation",zhDescription:"由确定性程序规则计算",enDescription:"Calculated by deterministic program rules"},
  AI_SYNTHESIS:{glyph:"✦",zh:"AI 综合",en:"AI synthesis",zhDescription:"模型基于证据形成的综合判断",enDescription:"Model synthesis grounded in evidence"},
  UNCERTAINTY:{glyph:"?",zh:"未确认",en:"Uncertainty",zhDescription:"尚未确认或受置信度限制",enDescription:"Unconfirmed or confidence-limited"},
  COUNTEREVIDENCE:{glyph:"↯",zh:"反向证据",en:"Counterevidence",zhDescription:"与主要判断相反或限制其成立的证据",enDescription:"Evidence opposing or limiting the primary view"},
  MISSING_DATA:{glyph:"!",zh:"数据缺失",en:"Missing data",zhDescription:"数据缺失、过期或质量不足",enDescription:"Missing, stale or insufficient-quality data"},
};
export function SemanticBadge({ kind, language }: { kind:SemanticKind; language:UiLanguage }) { const item=semantic[kind]; const label=item[language]; const description=language === "zh" ? item.zhDescription : item.enDescription; return <span className={`ama-semantic ama-semantic-${kind.toLowerCase().replace(/_/g,"-")}`} aria-label={`${label}：${description}`} data-semantic-kind={kind}><span aria-hidden="true" className="ama-semantic-glyph">{item.glyph}</span>{label}<span className="sr-only">。{description}</span></span>; }
export function SemanticBlock({ kind, language, children, className="" }: React.PropsWithChildren<{kind:SemanticKind;language:UiLanguage;className?:string}>) { return <div className={`ama-semantic-block ${className}`} data-semantic-kind={kind}><SemanticBadge kind={kind} language={language}/>{children}</div>; }
export const semanticKinds = Object.keys(semantic) as SemanticKind[];
