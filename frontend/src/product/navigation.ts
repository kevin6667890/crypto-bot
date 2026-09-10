export type ProductDomain = "crypto" | "prediction-markets";
export type WorkflowPage = "test" | "tracking" | "changes" | "advanced";

export type NavigationState = {
  domain: ProductDomain;
  page?: WorkflowPage;
};

/**
 * Keeps domain selection separate from the Crypto workflow destination.
 * The Crypto home intentionally has no active workflow page.
 */
export function navigationState(active: string): NavigationState {
  if (active === "prediction-markets") return { domain: "prediction-markets" };
  if (active === "test" || active === "changes" || active === "advanced") {
    return { domain: "crypto", page: active };
  }
  if (active === "tracking" || active === "track-detail") {
    return { domain: "crypto", page: "tracking" };
  }
  return { domain: "crypto" };
}
