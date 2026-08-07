interface ImportMetaEnv {
  readonly VITE_PAPER_API_URL?: string;
  readonly VITE_AI_MARKET_ANALYSIS_SHADOW_ENABLED?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
