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

export type CoordinateLabel = PriceLabelValue & {
  coordinate: number;
  order: number;
};

export type PositionedPriceLabel = PriceLabelValue & {
  top: number;
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
 * Keeps the configured series order deterministic while moving only the label
 * boxes. Values and their price-derived target coordinates are never changed.
 */
export function arrangePriceLabels(
  labels: CoordinateLabel[],
  minGap = 21,
  topBoundary = 11,
  bottomBoundary = Number.POSITIVE_INFINITY,
): PositionedPriceLabel[] {
  if (!labels.length) return [];
  const sorted = [...labels].sort((left, right) =>
    left.coordinate - right.coordinate || left.order - right.order);
  const tops = sorted.map(label =>
    Math.min(bottomBoundary, Math.max(topBoundary, label.coordinate)));

  for (let index = 1; index < tops.length; index += 1) {
    tops[index] = Math.max(tops[index], tops[index - 1] + minGap);
  }
  if (tops[tops.length - 1] > bottomBoundary) {
    tops[tops.length - 1] = bottomBoundary;
    for (let index = tops.length - 2; index >= 0; index -= 1) {
      tops[index] = Math.min(tops[index], tops[index + 1] - minGap);
    }
    if (tops[0] < topBoundary) {
      const shift = topBoundary - tops[0];
      for (let index = 0; index < tops.length; index += 1) tops[index] += shift;
    }
  }

  return sorted
    .map((label, index) => ({
      id: label.id,
      name: label.name,
      color: label.color,
      value: label.value,
      top: tops[index],
      order: label.order,
    }))
    .sort((left, right) => left.order - right.order)
    .map(({ order: _order, ...label }) => label);
}

export function formatChartPrice(value: number, precision = 2) {
  return value.toLocaleString(undefined, {
    minimumFractionDigits: precision,
    maximumFractionDigits: precision,
  });
}
