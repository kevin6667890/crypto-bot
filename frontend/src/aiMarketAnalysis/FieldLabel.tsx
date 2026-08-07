import { translateField } from "./fieldTranslations";
import type { UiLanguage } from "./enumTranslations";
export function FieldLabel({ field, language }: {field:string;language:UiLanguage}) { return <>{translateField(field,language)}</>; }

