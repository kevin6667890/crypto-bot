import { translateEnum, type UiLanguage } from "./enumTranslations";
import type { EnumGroup } from "./enumManifest.generated";
export function EnumLabel({ group, value, language }: {group:EnumGroup;value:unknown;language:UiLanguage}) { return <>{translateEnum(group,value,language)}</>; }

