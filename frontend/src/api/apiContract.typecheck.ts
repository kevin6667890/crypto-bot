import type { components } from "./generated";
import type { UnixMilliseconds, UnixSeconds } from "./time";

type FlowHistory = components["schemas"]["FlowHistoryResponse"];
type TrendHistory = components["schemas"]["OperationsTrends"];

const flowContract = { points: [{ time: 1, value: 2 }] } as FlowHistory;
const trendContract: TrendHistory = { enabled: true, window: "24h", points: [] };
void flowContract;
void trendContract;

// @ts-expect-error Flow history uses `points`; accepting `data` would hide schema drift.
const invalidFlow: FlowHistory = { data: [] };
// @ts-expect-error Operations trends uses `points`; accepting `data` would hide schema drift.
const invalidTrends: TrendHistory = { enabled: true, window: "24h", data: [] };
void invalidFlow;
void invalidTrends;

declare const seconds: UnixSeconds;
declare const milliseconds: UnixMilliseconds;
// @ts-expect-error Seconds and milliseconds require an explicit conversion.
const invalidMilliseconds: UnixMilliseconds = seconds;
// @ts-expect-error Milliseconds and seconds require an explicit conversion.
const invalidSeconds: UnixSeconds = milliseconds;
void invalidMilliseconds;
void invalidSeconds;
