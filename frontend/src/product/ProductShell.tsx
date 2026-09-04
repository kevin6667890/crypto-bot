import { FlaskConical } from "lucide-react";
import { useLanguage } from "../i18n";
import { productText } from "./i18n";
import { navigationState, type WorkflowPage } from "./navigation";

const workflowLinks: Array<{ page: WorkflowPage; href: string; label: "test" | "tracking" | "changed" | "advanced" }> = [
  { page: "test", href: "/test-an-idea", label: "test" },
  { page: "tracking", href: "/tracking", label: "tracking" },
  { page: "changes", href: "/what-changed", label: "changed" },
  { page: "advanced", href: "/advanced", label: "advanced" },
];

export default function ProductShell({ active, children }: { active: string; children: React.ReactNode }) {
  const { language, setLanguage } = useLanguage();
  const text = productText(language);
  const state = navigationState(active);
  return <div className="product-app">
    <header className="product-header">
      <a className="product-brand" href="/"><FlaskConical size={18} /><strong>Crypto-Bot</strong><span>{text.brand}</span></a>
      <nav className="product-navigation" aria-label={text.navigation}>
        <div className="domain-switcher" aria-label={text.domainNavigation}>
          <a className={state.domain === "crypto" ? "selected" : ""} data-selected={state.domain === "crypto" || undefined} aria-current={active === "home" ? "page" : undefined} href="/">{text.crypto}</a>
          <a className={state.domain === "prediction-markets" ? "selected" : ""} data-selected={state.domain === "prediction-markets" || undefined} aria-current={active === "prediction-markets" ? "page" : undefined} href="/prediction-markets">{text.predictionMarkets}</a>
        </div>
        {state.domain === "crypto" && <div className="workflow-navigation" aria-label={text.workflowNavigation}>
          {workflowLinks.map(({ page, href, label }) => <a key={page} className={state.page === page ? "active" : ""} aria-current={state.page === page ? "page" : undefined} href={href}>{text[label]}</a>)}
        </div>}
      </nav>
      <div className="product-language" role="group" aria-label={text.language}>
        <button className={language === "en" ? "active" : ""} onClick={() => setLanguage("en")}>EN</button>
        <button className={language === "zh" ? "active" : ""} onClick={() => setLanguage("zh")}>中文</button>
      </div>
    </header>
    {children}
  </div>;
}
