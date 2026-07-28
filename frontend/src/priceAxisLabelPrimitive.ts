import type {
  ISeriesApi,
  ISeriesPrimitive,
  ISeriesPrimitiveAxisView,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from "lightweight-charts";
import type { NativePriceAxisLabelOptions } from "./priceLabels";

/**
 * Official Lightweight Charts series primitive that contributes only a price
 * axis view. The library owns layout, clipping, and collision avoidance.
 */
export class PriceAxisLabelPrimitive implements ISeriesPrimitive<Time> {
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private requestUpdate: () => void = () => undefined;
  private price = 0;
  private color: string;
  private isVisible = false;
  private readonly view: ISeriesPrimitiveAxisView;
  private readonly views: readonly ISeriesPrimitiveAxisView[];

  constructor({ color }: { color: string }) {
    this.color = color;
    this.view = {
      coordinate: () => Number(this.series?.priceToCoordinate(this.price) ?? -10_000),
      text: () => this.series?.priceFormatter().format(this.price) ?? this.price.toFixed(2),
      textColor: () => "#ffffff",
      backColor: () => this.color,
      visible: () => this.isVisible,
      tickVisible: () => false,
    };
    this.views = [this.view];
  }

  attached(param: SeriesAttachedParameter<Time, SeriesType>) {
    this.series = param.series;
    this.requestUpdate = param.requestUpdate;
  }

  detached() {
    this.series = null;
    this.requestUpdate = () => undefined;
  }

  priceAxisViews() {
    return this.views;
  }

  applyOptions(options: NativePriceAxisLabelOptions) {
    this.price = options.price;
    this.color = options.axisLabelColor;
    this.isVisible = options.axisLabelVisible;
    this.requestUpdate();
  }
}
