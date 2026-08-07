export const shortId = (value?: string | null) => value ? `${value.slice(0, 12)}…` : "—";
export const formatTime = (value: string | number | undefined, locale: string) => value == null ? "—" : new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }).format(new Date(typeof value === "number" ? value * 1000 : value));
export const valueText = (value: unknown) => typeof value === "string" ? value : value == null ? "—" : JSON.stringify(value);
export const warningPriority = (warning: string) => /CRITICAL|WATERMARK_MISMATCH|SCHEMA_UPGRADE/.test(warning) ? 0 : /MAJOR|GAP|STALE|PARTIAL|MISSING/.test(warning) ? 1 : 2;
export const safeEnum = (value: unknown) => typeof value === "string" ? value.split("_").join(" ") : "—";
