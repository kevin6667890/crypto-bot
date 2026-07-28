export type TimestampValue = { time: number; value: number };

export type PriceLabelSource = {
  id: string;
  name: string;
  color: string;
  values: TimestampValue[];
};

export type PriceLabelValue = {
  id: string;
  name: string;
  color: string;
  value: number;
};

export type NativePriceAxisLabelOptions = {
  price: number;
  color: string;
  axisLabelColor: string;
  lineVisible: false;
  axisLabelVisible: boolean;
};

export type NativePriceAxisLabel = {
  applyOptions(options: NativePriceAxisLabelOptions): void;
};

export function exactValuesAtTimestamp(
  timestamp: number,
  sources: PriceLabelSource[],
): PriceLabelValue[] {
  return sources.flatMap(source => {
    const point = source.values.find(item => Number(item.time) === timestamp);
    return point && Number.isFinite(point.value)
      ? [{ id: source.id, name: source.name, color: source.color, value: point.value }]
      : [];
  });
}

/**
 * Updates official Lightweight Charts price-axis labels. Missing exact
 * timestamps hide that series' label; no nearest or future value is selected.
 */
export function updateNativePriceAxisLabels(
  timestamp: number,
  sources: PriceLabelSource[],
  labels: Record<string, NativePriceAxisLabel>,
) {
  const exact = new Map(exactValuesAtTimestamp(timestamp, sources).map(value => [value.id, value]));
  sources.forEach(source => {
    const value = exact.get(source.id);
    labels[source.id]?.applyOptions({
      price: value?.value ?? 0,
      color: source.color,
      axisLabelColor: source.color,
      lineVisible: false,
      axisLabelVisible: !!value,
    });
  });
  return [...exact.values()];
}

export function latestValues(
  sources: PriceLabelSource[],
): PriceLabelValue[] {
  return sources.flatMap(source => {
    const point = [...source.values].reverse().find(item => Number.isFinite(item.value));
    return point
      ? [{ id: source.id, name: source.name, color: source.color, value: point.value }]
      : [];
  });
}

/** Restores each axis label to that series' own latest confirmed value. */
export function updateLatestNativePriceAxisLabels(
  sources: PriceLabelSource[],
  labels: Record<string, NativePriceAxisLabel>,
) {
  const latest = new Map(latestValues(sources).map(value => [value.id, value]));
  sources.forEach(source => {
    const value = latest.get(source.id);
    labels[source.id]?.applyOptions({
      price: value?.value ?? 0,
      color: source.color,
      axisLabelColor: source.color,
      lineVisible: false,
      axisLabelVisible: !!value,
    });
  });
  return [...latest.values()];
}
