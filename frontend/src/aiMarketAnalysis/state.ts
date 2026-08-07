export class RequestSequence {
  private sequence = 0;
  private controller?: AbortController;
  begin() { this.controller?.abort(); this.controller = new AbortController(); return { sequence: ++this.sequence, signal: this.controller.signal }; }
  accepts(sequence: number) { return sequence === this.sequence && !this.controller?.signal.aborted; }
  abort() { this.controller?.abort(); this.sequence += 1; }
}

export class PresentationCache<T extends { presentation_id: string }> {
  private values = new Map<string, T>();
  get(key: readonly unknown[]) { return this.values.get(JSON.stringify(key)); }
  set(key: readonly unknown[], value: T) { const serialized = JSON.stringify(key); const old = this.values.get(serialized); if (!old || old.presentation_id !== value.presentation_id) this.values.set(serialized, value); }
  clear() { this.values.clear(); }
}
