import { translateError, type UiLanguage } from "./enumTranslations";
export function ErrorCode({ code, language }: {code:string;language:UiLanguage}) { const parts=code.split(":");const stable=parts[parts.length-1]; return <span>{translateError(stable,language)} <code>{stable}</code></span>; }
