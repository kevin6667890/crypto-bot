import { translateWarning, type UiLanguage } from "./enumTranslations";
import { SemanticBadge } from "./SemanticBadge";
export function WarningItem({ code, language }: {code:string;language:UiLanguage}) { return <strong className="ama-warning-item"><SemanticBadge kind="MISSING_DATA" language={language}/><span>{translateWarning(code,language)}</span></strong>; }
