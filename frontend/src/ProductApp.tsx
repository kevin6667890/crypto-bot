import { Component, lazy, Suspense, useEffect, useState } from "react";
import ProductShell from "./product/ProductShell";
import "./product.css";

const LegacyApplication = lazy(() => import("./App"));
const ProductHome = lazy(() => import("./product/ProductHome"));
const TestAnIdeaPage = lazy(() => import("./thesis/TestAnIdeaPage"));
const TrackingPage = lazy(() => import("./tracking/TrackingPage"));
const TrackDetailPage = lazy(() => import("./tracking/TrackDetailPage"));
const WhatChangedPage = lazy(() => import("./tracking/WhatChangedPage"));

export type ProductRoute =
  | { kind: "home" }
  | { kind: "test" }
  | { kind: "tracking" }
  | { kind: "track-detail"; trackId: string }
  | { kind: "changes" }
  | { kind: "legacy" };

export function resolveProductRoute(pathname: string, hash = ""): ProductRoute {
  const path = (pathname.replace(/\/+$/, "") || "/").toLowerCase();
  const legacyHash = hash.toLowerCase();
  if (path === "/test-an-idea") return { kind: "test" };
  if (path === "/tracking") return { kind: "tracking" };
  const detail = path.match(/^\/tracking\/([^/]+)$/);
  if (detail) return { kind: "track-detail", trackId: decodeURIComponent(detail[1]) };
  if (path === "/what-changed") return { kind: "changes" };
  if (path === "/" && !legacyHash) return { kind: "home" };
  return { kind: "legacy" };
}

class ProductRouteBoundary extends Component<{ children: React.ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    return this.state.failed
      ? <main className="product-message" role="alert">This page is temporarily unavailable.</main>
      : this.props.children;
  }
}

function Deferred({ children }: { children: React.ReactNode }) {
  return <ProductRouteBoundary><Suspense fallback={<main className="product-message" role="status">Loading evidence…</main>}>{children}</Suspense></ProductRouteBoundary>;
}

export default function ProductApp() {
  const [, setLocationVersion] = useState(0);
  useEffect(() => {
    const sync = () => setLocationVersion((value) => value + 1);
    window.addEventListener("hashchange", sync); window.addEventListener("popstate", sync);
    return () => { window.removeEventListener("hashchange", sync); window.removeEventListener("popstate", sync); };
  }, []);
  const route = resolveProductRoute(window.location.pathname, window.location.hash);
  if (route.kind === "legacy") return <Deferred><LegacyApplication /></Deferred>;
  const content = route.kind === "home" ? <ProductHome />
    : route.kind === "test" ? <TestAnIdeaPage />
      : route.kind === "tracking" ? <TrackingPage />
        : route.kind === "track-detail" ? <TrackDetailPage trackId={route.trackId} />
          : <WhatChangedPage />;
  return <ProductShell active={route.kind}><Deferred>{content}</Deferred></ProductShell>;
}
