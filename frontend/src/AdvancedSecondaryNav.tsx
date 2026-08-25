import { useLanguage } from "./i18n";

export type AdvancedPage = "workspace" | "market" | "research" | "microstructure" | "operations";

const destinations: AdvancedPage[] = ["workspace", "market", "research", "microstructure", "operations"];

export default function AdvancedSecondaryNav({ active, onNavigate }: {
  active: AdvancedPage;
  onNavigate: (page: AdvancedPage) => void;
}) {
  const { t } = useLanguage();
  return <nav className="advanced-secondary-nav" aria-label={t("advanced.secondaryNav" as never)}>
      {destinations.map((page) => <a
        aria-current={active === page ? "page" : undefined}
        className={active === page ? "active" : ""}
        href={`/advanced#${page}`}
        key={page}
        onClick={(event) => {
          event.preventDefault();
          onNavigate(page);
        }}
      >{t(`nav.${page}` as never)}</a>)}
    </nav>;
}
