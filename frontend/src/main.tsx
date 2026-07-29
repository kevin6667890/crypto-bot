import React, { Suspense, lazy } from "react";
import ReactDOM from "react-dom/client";
import { LanguageProvider } from "./i18n";
import "./styles.css";

const App = lazy(() => import("./App"));

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <LanguageProvider>
      <Suspense fallback={<div className="route-loading" role="status">LOADING · Market Analysis</div>}>
        <App />
      </Suspense>
    </LanguageProvider>
  </React.StrictMode>,
);
