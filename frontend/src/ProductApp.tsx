import { Component, lazy, Suspense, useEffect, useState } from "react";
import { useLanguage } from "./i18n";
import ProductShell from "./product/ProductShell";
import { productText } from "./product/i18n";
import "./product.css";

const LegacyApplication = lazy(() => import("./App"));
const ProductHome = lazy(() => import("./product/ProductHome"));
const TestAnIdeaPage = lazy(() => import("./thesis/TestAnIdeaPage"));
const TrackingPage = lazy(() => import("./tracking/TrackingPage"));
const TrackDetailPage = lazy(() => import("./tracking/TrackDetailPage"));
const WhatChangedPage = lazy(() => import("./tracking/WhatChangedPage"));
const AdvancedLanding = lazy(() => import("./product/AdvancedLanding"));
const PredictionMarkets = lazy(() => import("./PredictionMarkets"));
const FRONTEND_BUILD_ID = "2026-08-25T08:42:00Z";

type PredictionMarketsView = "overview" | "markets" | "forecasts" | "scoreboard";

export type ProductRoute =
  | { kind: "home" }
  | { kind: "test" }
  | { kind: "tracking" }
  | { kind: "track-detail"; trackId: string }
  | { kind: "changes" }
  | { kind: "advanced" }
  | { kind: "prediction-markets"; view: PredictionMarketsView }
  | { kind: "legacy" };

export function resolveProductRoute(pathname: string, hash = ""): ProductRoute {
  const path = (pathname.replace(/\/+$/, "") || "/").toLowerCase();
  const legacyHash = hash.toLowerCase();
  const predictionMarkets = path.match(/^\/prediction-markets(?:\/(markets|forecasts|scoreboard))?$/);
  if (predictionMarkets) return {
    kind: "prediction-markets",
    view: (predictionMarkets[1] || "overview") as PredictionMarketsView,
  };
  if (path === "/test-an-idea") return { kind: "test" };
  if (path === "/tracking") return { kind: "tracking" };
  const detail = path.match(/^\/tracking\/([^/]+)$/);
  if (detail) return { kind: "track-detail", trackId: decodeURIComponent(detail[1]) };
  if (path === "/what-changed") return { kind: "changes" };
  if (path === "/advanced" && !legacyHash) return { kind: "advanced" };
  if (path === "/" && !legacyHash) return { kind: "home" };
  return { kind: "legacy" };
}

class ProductRouteBoundary extends Component<{ children: React.ReactNode; error: string }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    return this.state.failed
      ? <main className="product-message" role="alert">{this.props.error}</main>
      : this.props.children;
  }
}

function Deferred({ children }: { children: React.ReactNode }) {
  const { language } = useLanguage();
  const text = productText(language);
  return <ProductRouteBoundary error={text.routeUnavailable}><Suspense fallback={<main className="product-message" role="status">{text.loadingEvidence}</main>}>{children}</Suspense></ProductRouteBoundary>;
}

export default function ProductApp() {
  const [, setLocationVersion] = useState(0);
  useEffect(() => {
    document.documentElement.dataset.frontendBuild = FRONTEND_BUILD_ID;
    const sync = () => setLocationVersion((value) => value + 1);
    window.addEventListener("hashchange", sync); window.addEventListener("popstate", sync);
    return () => { window.removeEventListener("hashchange", sync); window.removeEventListener("popstate", sync); };
  }, []);
  const route = resolveProductRoute(window.location.pathname, window.location.hash);
  if (route.kind === "legacy") return <ProductShell active="advanced"><Deferred><LegacyApplication /></Deferred></ProductShell>;
  if (route.kind === "prediction-markets") {
    const navigate = (view: PredictionMarketsView) => {
      const path = `/prediction-markets${view === "overview" ? "" : `/${view}`}`;
      window.history.pushState({}, "", path);
      setLocationVersion((value) => value + 1);
    };
    return <ProductShell active="prediction-markets"><Deferred><PredictionMarkets view={route.view} navigate={navigate} /></Deferred></ProductShell>;
  }
  const content = route.kind === "home" ? <ProductHome />
    : route.kind === "test" ? <TestAnIdeaPage />
      : route.kind === "tracking" ? <TrackingPage />
        : route.kind === "track-detail" ? <TrackDetailPage trackId={route.trackId} />
          : route.kind === "changes" ? <WhatChangedPage />
            : <AdvancedLanding />;
  return <ProductShell active={route.kind}><Deferred>{content}</Deferred></ProductShell>;
}
