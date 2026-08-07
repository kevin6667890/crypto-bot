import { shortId, valueText } from "./formatters";
import { translateField } from "./fieldTranslations";
import type { UiLanguage } from "./enumTranslations";
import type { ShadowLabels } from "./i18n";
import type { Presentation } from "./types";
export function ProvenancePanel({value,labels,language}:{value:Presentation;labels:ShadowLabels;language:UiLanguage}){const rows:{field:string;value:unknown}[]=[{field:"report_version",value:value.report_schema_version},{field:"prompt_version",value:value.report?.prompt_version},{field:"provider_model",value:value.report?.model},{field:"context_id",value:shortId(value.context_id)},{field:"registry_snapshot_id",value:shortId(value.registry_snapshot_id)},{field:"audit_version",value:value.audit_schema_version},{field:"policy_version",value:value.audit_summary?.policy_version},{field:"source_versions",value:value.source_versions},{field:"presentation_hash",value:shortId(value.presentation_hash)}];return <details className="ama-panel"><summary>{labels.provenance}</summary><dl className="ama-ratios">{rows.map(row=><div key={row.field}><dt>{translateField(row.field,language)}</dt><dd>{valueText(row.value)}</dd></div>)}</dl></details>;}
