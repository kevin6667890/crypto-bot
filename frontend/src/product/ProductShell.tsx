import { FlaskConical } from "lucide-react";
import { useLanguage } from "../i18n";
import { productText } from "./i18n";

export default function ProductShell({ active, children }: { active: string; children: React.ReactNode }) {
  const { language, setLanguage } = useLanguage();
  const text = productText(language);
  return <div className="product-app">
    <header className="product-header">
      <a className="product-brand" href="/"><FlaskConical size={18} /><strong>Crypto-Bot</strong><span>{text.brand}</span></a>
      <nav aria-label={text.navigation}>
        <a className={active === "home" ? "active" : ""} href="/">{text.home}</a>
        <a className={`primary ${active === "test" ? "active" : ""}`} href="/test-an-idea">{text.test}</a>
        <a className={active.startsWith("track") ? "active" : ""} href="/tracking">{text.tracking}</a>
        <a className={active === "changes" ? "active" : ""} href="/what-changed">{text.changed}</a>
        <a href="/advanced">{text.advanced}</a>
      </nav>
      <div className="product-language" role="group" aria-label={text.language}>
        <button className={language === "en" ? "active" : ""} onClick={() => setLanguage("en")}>EN</button>
        <button className={language === "zh" ? "active" : ""} onClick={() => setLanguage("zh")}>中文</button>
      </div>
    </header>
    {children}
  </div>;
}
