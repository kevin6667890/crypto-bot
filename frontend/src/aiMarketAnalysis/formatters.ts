export const shortId = (value?: string | null) => value ? `${value.slice(0, 12)}…` : "—";
export const formatTime = (value: string | number | undefined, locale: string) => value == null ? "—" : new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }).format(new Date(typeof value === "number" ? value * 1000 : value));
export const valueText = (value: unknown) => typeof value === "string" ? value : value == null ? "—" : JSON.stringify(value);
export const warningPriority = (warning: string) => /CRITICAL|WATERMARK_MISMATCH|SCHEMA_UPGRADE/.test(warning) ? 0 : /MAJOR|GAP|STALE|PARTIAL|MISSING/.test(warning) ? 1 : 2;
/** Developer-only diagnostic fallback. Business enums must use translateEnum. */
export const safeEnum = (value: unknown) => typeof value === "string" ? `UNKNOWN_ENUM(${value})` : "—";
export const localizedValue = (value: unknown, language: UiLanguage): string => {
  if (value == null) return "—";
  if (Array.isArray(value)) return value.map(item=>localizedValue(item,language)).join(" · ");
  if (typeof value === "object") return Object.values(value as Record<string,unknown>).map(item=>localizedValue(item,language)).join(" · ");
  if (typeof value === "boolean") return language === "zh" ? (value?"是":"否") : (value?"Yes":"No");
  return typeof value === "string" ? translateKnownEnum(value,language) : String(value);
};
import { translateKnownEnum, type UiLanguage } from "./enumTranslations";
