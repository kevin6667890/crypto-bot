import { describe, expect, it } from "vitest";
import { formatTime, shortId, warningPriority } from "./formatters";
import { presentationKey } from "./queryKeys";
import { orderedSections } from "./ReportSections";
import { PresentationCache, RequestSequence } from "./state";
import { freshnessStates, instruments, isSafeHttpUrl, modes, parsePresentation } from "./types";

const passed = () => ({
  presentation_schema_version:"ai-market-presentation-v1",presentation_id:"p1",instrument:"ETH-USDT-SWAP",mode:"FULL",language:"zh-CN",
  report_id:"r",request_id:"q",context_id:"c",eligibility:"AUDIT_PASSED_SHADOW_ONLY",freshness:{status:"CURRENT",policy_version:"f1"},
  latest_generated:{report_id:"r",eligibility:"AUDIT_PASSED_SHADOW_ONLY"},report:{context_id:"c",schema_version:"v",headline:"<script>alert(1)</script>",market_phase:"MIXED",directional_bias:"BULLISH",confidence:"MEDIUM",sections:[],key_levels:[],scenarios:[],data_warnings:[],source_versions:{},language:"zh-CN"},audit_summary:null,referenced_facts:[],referenced_levels:[],referenced_scenarios:[],referenced_macro:[],position_summary:null,data_warnings:[],health_summary:{},source_versions:{},presentation_hash:"h",
});

describe("AI market presentation contract", () => {
  it("accepts all explicit instruments without aliases", () => expect(instruments).toEqual(["BTC-USDT-SWAP","ETH-USDT-SWAP","SOL-USDT-SWAP"]));
  it("keeps report modes isolated", () => expect(modes).toEqual(["QUICK","FULL","POSITION_AWARE"]));
  it("recognizes every freshness enum", () => expect(freshnessStates).toHaveLength(5));
  it("parses a passed atomic projection", () => expect(parsePresentation(passed()).presentation_id).toBe("p1"));
  it("rejects report bodies for pending reports", () => expect(() => parsePresentation({...passed(),eligibility:"AUDIT_PENDING"})).toThrow("UNAUDITED_REPORT_BODY_REJECTED"));
  it("rejects cross-context report bodies", () => expect(() => parsePresentation({...passed(),context_id:"other"})).toThrow("PRESENTATION_CONTEXT_MISMATCH"));
  it("rejects SOL masquerading as ETH", () => expect(() => parsePresentation({...passed(),instrument:"SOL-USDT"})).toThrow("INVALID_PRESENTATION_CONTRACT"));
});

describe("race and cache isolation", () => {
  it("aborts the previous request", () => { const s=new RequestSequence();const first=s.begin();s.begin();expect(first.signal.aborted).toBe(true); });
  it("rejects a late response sequence", () => { const s=new RequestSequence();const first=s.begin();s.begin();expect(s.accepts(first.sequence)).toBe(false); });
  it("separates instrument mode language report and auth scope", () => { const eth=presentationKey({instrument:"ETH-USDT-SWAP",mode:"FULL",language:"zh-CN",adminScope:"a"});const sol=presentationKey({instrument:"SOL-USDT-SWAP",mode:"FULL",language:"zh-CN",adminScope:"a"});const position=presentationKey({instrument:"ETH-USDT-SWAP",mode:"POSITION_AWARE",language:"zh-CN",adminScope:"a"});expect(new Set([JSON.stringify(eth),JSON.stringify(sol),JSON.stringify(position)]).size).toBe(3); });
  it("invalidates a cache entry on presentation identity", () => { const c=new PresentationCache<{presentation_id:string,value:number}>();const k=["x"];c.set(k,{presentation_id:"1",value:1});c.set(k,{presentation_id:"2",value:2});expect(c.get(k)?.value).toBe(2); });
});

describe("formatting and safe rendering", () => {
  it("orders report sections by contract", () => expect(orderedSections([{section_id:"SCENARIOS"},{section_id:"CONCLUSION"}]).map(x=>x.section_id)).toEqual(["CONCLUSION","SCENARIOS"]));
  it("prioritizes critical warnings", () => expect(warningPriority("CRITICAL_GAP")).toBeLessThan(warningPriority("NO_MACRO")));
  it("formats stable short ids", () => expect(shortId("1234567890123456")).toBe("123456789012…"));
  it("formats UTC timestamps", () => expect(formatTime(0,"en")).toContain("1970"));
  it.each(["javascript:alert(1)","data:text/html,x","file:///secret","not a url"])("rejects unsafe URL %s", url => expect(isSafeHttpUrl(url)).toBe(false));
  it.each(["https://example.com/evidence","http://localhost/evidence"])("accepts HTTP evidence URL %s", url => expect(isSafeHttpUrl(url)).toBe(true));
  it("renders provider text as a string contract", () => expect(parsePresentation(passed()).report?.headline).toBe("<script>alert(1)</script>"));
});
