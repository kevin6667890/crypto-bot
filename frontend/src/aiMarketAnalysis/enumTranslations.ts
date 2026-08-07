import { enumManifest, type EnumGroup } from "./enumManifest.generated";

export type UiLanguage = "zh" | "en";

const exact: Record<string, readonly [string, string]> = {
  "zh-CN": ["中文", "Chinese"], en: ["英文", "English"],
  QUICK: ["快速", "Quick"], FULL: ["完整", "Full"], POSITION_AWARE: ["持仓感知", "Position-aware"],
  AUDIT_PENDING: ["等待审计", "Waiting for audit"], AUDIT_PASSED_SHADOW_ONLY: ["审计通过（仅 Shadow）", "Audit passed (Shadow only)"],
  AUDIT_FAILED: ["审计未通过", "Audit failed"], AUDIT_ERROR: ["审计系统错误", "Audit system error"],
  AUDIT_NOT_FOUND: ["尚未创建审计", "Audit not created"], AUDIT_SCHEMA_UPGRADE_REQUIRED: ["需要 Schema 升级", "Schema upgrade required"],
  CURRENT: ["当前", "Current"], AGING: ["正在变旧", "Aging"], STALE: ["已过期", "Stale"], SUPERSEDED: ["已被替代", "Superseded"],
  UNKNOWN: ["未知", "Unknown"], VALID: ["有效", "Valid"], PARTIAL: ["部分可用", "Partial"], PARTIAL_AFTER_GAP: ["缺口后部分可用", "Partial after gap"], MISSING: ["缺失", "Missing"], UNAVAILABLE: ["不可用", "Unavailable"],
  STRONG_BULL: ["强势多头", "Strong bull"], BULL: ["多头", "Bull"], BULLISH: ["看多", "Bullish"],
  STRONG_BEAR: ["强势空头", "Strong bear"], BEAR: ["空头", "Bear"], BEARISH: ["看空", "Bearish"],
  NEUTRAL: ["中性", "Neutral"], MIXED: ["混合", "Mixed"], RANGE: ["区间", "Range"],
  RISING: ["上升", "Rising"], FALLING: ["下降", "Falling"], FLAT: ["持平", "Flat"], LONG: ["多头", "Long"], SHORT: ["空头", "Short"],
  FROZEN_CONTEXT: ["冻结 Context", "Frozen context"], REGISTRY_SNAPSHOT: ["Registry 快照", "Registry snapshot"], MACRO_EVIDENCE: ["宏观证据", "Macro evidence"], POSITION_CONTEXT: ["持仓 Context", "Position context"],
  HH_HL: ["更高高点 / 更高低点", "Higher highs / higher lows"], LH_LL: ["更低高点 / 更低低点", "Lower highs / lower lows"],
  HIGH: ["高", "High"], MEDIUM: ["中", "Medium"], LOW: ["低", "Low"], INSUFFICIENT: ["证据不足", "Insufficient"], INSUFFICIENT_DATA: ["数据不足", "Insufficient data"],
  UP: ["向上", "Up"], DOWN: ["向下", "Down"], NONE: ["无", "None"],
  SUPPORT: ["支撑", "Support"], RESISTANCE: ["压力", "Resistance"], PIVOT: ["枢轴", "Pivot"],
  ACTIVE: ["有效", "Active"], BROKEN: ["已破坏", "Broken"], FLIPPED: ["角色翻转", "Flipped"], UNCONFIRMED: ["未确认", "Unconfirmed"], INVALIDATED: ["已失效", "Invalidated"],
  WEAK: ["弱", "Weak"], MODERATE: ["中等", "Moderate"], STRONG: ["强", "Strong"], MAJOR: ["主要", "Major"],
  PAPER: ["Paper 模拟持仓", "Paper simulated position"], USER_DECLARED: ["用户声明持仓", "User-declared position"],
  NEW_LONGS_DOMINANT: ["新增多头主导", "New longs dominant"], SHORT_COVERING_DOMINANT: ["空头回补主导", "Short covering dominant"],
  NEW_SHORTS_DOMINANT: ["新增空头主导", "New shorts dominant"], LONG_UNWINDING_DOMINANT: ["多头减仓主导", "Long unwinding dominant"],
  SHORT_LIQUIDATION_ASSISTED: ["空头清算辅助推动", "Short liquidations assisted"], LONG_LIQUIDATION_ASSISTED: ["多头清算辅助推动", "Long liquidations assisted"],
  TWO_SIDED_DELEVERAGING: ["双向去杠杆", "Two-sided deleveraging"], SPOT_BUYING_LIKELY: ["现货买盘可能主导", "Spot buying likely"],
  SPOT_SELLING_LIKELY: ["现货卖盘可能主导", "Spot selling likely"], LEVERAGED_LONG_BUILDUP: ["杠杆多头累积", "Leveraged long buildup"],
  LEVERAGED_SHORT_BUILDUP: ["杠杆空头累积", "Leveraged short buildup"], MIXED_POSITIONING: ["仓位变化混合", "Mixed positioning"],
  INSUFFICIENT_EVIDENCE: ["证据不足", "Insufficient evidence"], ACTIVE_BUYING_CONTRIBUTED: ["主动买盘有所贡献", "Active buying contributed"],
  BULLISH_CONTINUATION: ["看多延续", "Bullish continuation"], NORMAL_RETEST: ["正常回踩", "Normal retest"], FAILED_BREAKOUT: ["突破失败", "Failed breakout"],
  BEARISH_CONTINUATION: ["看空延续", "Bearish continuation"], NORMAL_BEARISH_RETEST: ["正常反抽", "Normal bearish retest"], FAILED_BREAKDOWN: ["跌破失败", "Failed breakdown"],
  POST_BREAKOUT_PULLBACK: ["突破后回踩", "Post-breakout pullback"], RANGE_BUILDING: ["区间构建", "Range building"], COMPRESSION: ["收敛", "Compression"],
  BREAKOUT_ATTEMPT: ["突破尝试", "Breakout attempt"], BREAKOUT_CONFIRMED: ["突破已确认", "Breakout confirmed"], IMPULSE: ["推动", "Impulse"], RETEST: ["回测", "Retest"], CONTINUATION: ["延续", "Continuation"], REVERSAL: ["反转", "Reversal"], UNCLASSIFIED: ["未分类", "Unclassified"], EXPIRED: ["已到期", "Expired"],
  PLAN_COMPLETED: ["计划已完成", "Plan completed"], PLAN_MOSTLY_COMPLETED: ["计划基本完成", "Plan mostly completed"], STOP_INVALIDATED: ["原止损已失效", "Original stop invalidated"],
  CVD_GAP: ["CVD 数据缺口", "CVD data gap"], OI_GAP: ["OI 数据缺口", "OI data gap"], DATA_GAP: ["数据缺口", "Data gap"],
  CRITICAL: ["严重", "Critical"],
  WATERMARK_MISMATCH: ["水位时间不一致", "Watermark mismatch"], FORWARD_ONLY: ["仅前向数据", "Forward-only data"], WARMUP_INCOMPLETE: ["预热未完成", "Warmup incomplete"],
  NO_MACRO: ["无已验证宏观证据", "No verified macro evidence"], NO_POSITION: ["无持仓信息", "No position information"], CONFIDENCE_CEILING: ["置信度受限", "Confidence ceiling"], SCHEMA_UPGRADE: ["Schema 需要升级", "Schema upgrade required"],
  LEVEL_PROJECTION_MISSING: ["关键位投影缺失", "Level projection missing"], SCENARIO_PROJECTION_MISSING: ["Scenario 投影缺失", "Scenario projection missing"],
};

const zhTokens: Record<string, string> = {
  REPORT:"报告", HASH:"哈希", MISMATCH:"不匹配", CONTEXT:"上下文", REGISTRY:"注册表", SNAPSHOT:"快照", AUDIT:"审计", PAYLOAD:"载荷", LEVEL:"关键位", PROJECTION:"投影", SCENARIO:"路径", NUMERIC:"数值", GROUNDING:"依据", REFERENCE:"引用", SUPPORT:"支撑", INVALIDATION:"失效", COVERAGE:"覆盖", WARNING:"警告", CONTRADICTION:"矛盾", DETECTED:"发现", SCHEMA:"Schema", UPGRADE:"升级", REQUIRED:"需要", PRICE:"价格", OI:"OI", FLAT:"横盘", FUNDING:"资金费率", BASIS:"基差", LIQUIDATION:"清算", LONG:"多头", SHORT:"空头", DOMINANT:"主导", BALANCED:"均衡", ELEVATED:"升高", POSITIVE:"正", NEGATIVE:"负", EXTREME:"极端", CONTANGO:"升水", BACKWARDATION:"贴水", WIDENING:"扩大", NARROWING:"收窄", VERY:"非常", LOW:"低", CONTRACTING:"收缩", NORMAL:"正常", EXPANDING:"扩张", CLIMACTIC:"高潮", EXPANSION:"扩张", CONTRACTION:"收缩", AVAILABLE:"可用", REQUESTED:"请求", OFFICIAL:"官方", PRIMARY:"一手", DATA:"数据", REPUTABLE:"可信", NEWS:"新闻", SECONDARY:"二手", RESEARCH:"研究", USER:"用户", SUPPLIED:"提供", MONETARY:"货币", POLICY:"政策", INFLATION:"通胀", LABOUR:"劳动力", MARKET:"市场", LIQUIDITY:"流动性", ETF:"ETF", FLOW:"流向", REGULATION:"监管", EXCHANGE:"交易所", EVENT:"事件", PROTOCOL:"协议", ONCHAIN:"链上", RISK:"风险", ASSET:"资产", SENTIMENT:"情绪", OTHER:"其他", RANGE:"区间", HIGH:"高点", BREAKOUT:"突破", BOUNDARY:"边界", RETEST:"回测", ZONE:"区域", IMPULSE:"推动", CONFIRMED:"已确认", SWING:"摆动", ROLLING:"滚动", PREVIOUS:"前一", DAY:"日", WEEK:"周", PSYCHOLOGICAL:"心理", MOVING:"移动", AVERAGE:"均线", INVALID:"无效", ENABLED:"已启用", WORKER:"Worker", PROVIDER:"Provider", CONFIGURED:"已配置", LIVE:"真实", ALLOWED:"允许", QUEUE:"队列", DEPTH:"深度", ACTIVE:"活跃", REQUESTS:"请求", OLDEST:"最早", QUEUED:"排队", AGE:"时长", LAST:"最近", SUCCESS:"成功", FAILED:"失败", COUNT:"数量", BUDGET:"预算", BLOCKED:"阻断", DAILY:"每日", TOKENS:"Token", DB:"数据库", SIZE:"大小", VERSIONS:"版本", ID:"ID", VERSION:"版本", MODEL:"模型", SOURCE:"来源", TIMEFRAME:"周期", DYNAMIC:"动态", STATIC:"静态", VALID:"有效", UNTIL:"至", SLOPE:"斜率", FIRST:"首次", TESTED:"测试", BROKEN:"破坏", FLIPPED:"翻转", QUALITY:"质量", DIRECTION:"方向", LIKELIHOOD:"证据倾向", STATUS:"状态", TRIGGER:"触发", TEXT:"条件", CONFIRMATION:"确认", EXPECTED:"预期", PATH:"路径", TARGET:"目标", REFS:"引用", CLOSE:"收盘", CVD:"CVD", COUNTEREVIDENCE:"反向证据", DETAILS:"详情", ORIGINAL:"原始", QUANTITY:"数量", REMAINING:"剩余", STOP:"止损", TARGETS:"目标", COMPLETION:"完成度", RATIO:"比例", THESIS:"逻辑"
};

const title = (value: string) => value.toLowerCase().split("_").map(word => word === "oi" || word === "cvd" || word === "db" || word === "id" ? word.toUpperCase() : word.charAt(0).toUpperCase()+word.slice(1)).join(" ");
const chinese = (value: string) => value.split("_").map(token => zhTokens[token] || token).join(" · ");

type Catalog = Record<EnumGroup, Record<string, { zh: string; en: string }>>;
export const enumTranslationCatalog = Object.fromEntries(Object.entries(enumManifest.groups).map(([group, values]) => [group,
  Object.fromEntries(values.map(value => { const pair=exact[value]; return [value, { zh: pair?.[0] || chinese(value), en: pair?.[1] || title(value) }]; }))
])) as Catalog;

export function translateEnum(group: EnumGroup, value: unknown, language: UiLanguage): string {
  if (value == null || value === "") return "—";
  if (typeof value !== "string" || !enumManifest.groups[group].some(item => item === value as never)) return language === "zh" ? `未知枚举（${String(value)}）` : `Unknown enum (${String(value)})`;
  return enumTranslationCatalog[group][value][language];
}
export function translateKnownEnum(value: unknown, language: UiLanguage): string {
  if (typeof value !== "string") return String(value);
  for (const group of Object.keys(enumManifest.groups) as EnumGroup[]) if (enumManifest.groups[group].some(item=>item===value as never)) return enumTranslationCatalog[group][value][language];
  return value;
}
export const translateWarning = (code: unknown, language: UiLanguage) => translateEnum("data_warning", code, language);
export const translateError = (code: unknown, language: UiLanguage) => translateEnum("api_error_code", code, language);
