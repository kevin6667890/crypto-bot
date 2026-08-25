import React, { Suspense, lazy } from "react";
import ReactDOM from "react-dom/client";
import { LanguageProvider, useLanguage } from "./i18n";
import "./styles.css";

const App = lazy(() => import("./ProductApp"));

function RootFallback() {
  const { t } = useLanguage();
  return <div className="route-loading" role="status">{t("common.loading")} · {t("app.workspace")}</div>;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <LanguageProvider>
      <Suspense fallback={<RootFallback />}>
        <App />
      </Suspense>
    </LanguageProvider>
  </React.StrictMode>,
);
