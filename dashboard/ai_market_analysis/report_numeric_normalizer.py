"""Deterministic Arabic/Chinese market-number extraction and normalization."""
from __future__ import annotations
import re
from typing import Any
from .versions import AI_REPORT_NUMERIC_NORMALIZER_VERSION

CN_DIGITS={"零":0,"〇":0,"一":1,"二":2,"两":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9}
CN_UNITS={"十":10,"百":100,"千":1000,"万":10000,"亿":100000000}
CN_CHARS="零〇一二两三四五六七八九十百千万亿点"
ARABIC_RE=re.compile(r"(?<![A-Za-z_\d])(?P<approx>约|接近|附近|超过|低于)?\s*(?P<sign>[+-]|负)?\s*(?P<num>\d[\d, ]*(?:\.\d+)?)(?P<scale>[kK万亿])?\s*(?P<unit>%|个百分点|USDT|USD|美元|枚|张|合约|倍|R|ATR)?")
CHINESE_RE=re.compile(rf"(?P<approx>约|接近|附近|超过|低于)?(?P<prefix>百分之)?(?P<sign>负)?(?P<num>[{CN_CHARS}]+)(?P<unit>个百分点|美元|USDT|USD|枚|张|合约|倍|R)?")
RANGE_SEP_RE=re.compile(r"\s*(?:到|至|—|–|-)\s*")
EXCLUDED=re.compile(r"(?:\d{4}-\d{2}-\d{2}|\b(?:15m|1H|4H|1D|1W|v\d+(?:\.\d+)*|[A-Z_]+_\d+)\b|\d+\s*(?:分钟|小时|日|周))",re.I)

def chinese_to_number(token:str)->float:
    if not token or any(c not in CN_DIGITS and c not in CN_UNITS and c!="点" for c in token): raise ValueError("unparsed Chinese number")
    if "点" in token:
        left,right=token.split("点",1)
        if not right or any(c not in CN_DIGITS for c in right):raise ValueError("unparsed Chinese decimal")
        return chinese_to_number(left or "零")+float("0."+"".join(str(CN_DIGITS[c]) for c in right))
    if all(c in CN_DIGITS for c in token): return float("".join(str(CN_DIGITS[c]) for c in token))
    total=section=number=0
    for char in token:
        if char in CN_DIGITS:number=CN_DIGITS[char]
        else:
            unit=CN_UNITS[char]
            if unit<10000:section+=(number or 1)*unit
            else:section=(section+number)*unit;total+=section;section=0
            number=0
    return float(total+section+number)

def _unit(raw:str|None, percent:bool=False)->str|None:
    if percent or raw=="%":return "percent"
    return {"美元":"USDT","USD":"USDT","USDT":"USDT","枚":"coin","张":"contracts","合约":"contracts",
            "倍":"multiple","R":"R","ATR":"ATR","个百分点":"percentage_point"}.get(raw,raw)

CLOCK_RE = re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?\b")
UNICODE_TIMEFRAME_RE = re.compile(
    r"(?:15\s*\u5206\u949f|1\s*\u5c0f\u65f6|4\s*\u5c0f\u65f6|1\s*\u65e5|1\s*\u5468|\u65e5\u7ebf|\u5468\u7ebf)",
    re.I,
)
ENUMERATOR_RE = re.compile(r"(?:^|[\s\u3002\uff1a\uff1b:;.!?])\d{1,2}[\uff09)]")
VALID_UNTIL_RE = re.compile(r"(?:valid(?:\s+until)?|\u6709\u6548\u81f3)\s*\d{10}\b", re.I)

def _excluded(text:str,start:int,end:int)->bool:
    return (any(m.start()<end and start<m.end() for m in EXCLUDED.finditer(text))
            or any(m.start()<end and start<m.end() for pattern in (
                CLOCK_RE, UNICODE_TIMEFRAME_RE, ENUMERATOR_RE, VALID_UNTIL_RE
            ) for m in pattern.finditer(text)))

def normalize_numbers(text:str)->list[dict[str,Any]]:
    values=[]
    for pattern,kind in ((ARABIC_RE,"ARABIC"),(CHINESE_RE,"CHINESE")):
        for m in pattern.finditer(text):
            if _excluded(text,m.start(),m.end()):continue
            if kind=="CHINESE" and (m.group("num")=="点" or (len(m.group("num"))==1 and not any(m.groupdict().get(k) for k in ("approx","prefix","sign","unit")))):continue
            try:
                if kind=="ARABIC":
                    raw=m.group("num").replace(",","").replace(" ",""); value=float(raw)
                    scale=m.group("scale"); value*= {"k":1000,"K":1000,"万":10000,"亿":100000000}.get(scale,1)
                    percent=m.group("unit")=="%"
                else:value=chinese_to_number(m.group("num"));percent=bool(m.group("prefix"))
                if m.group("sign") in {"-","负"}:value=-value
                values.append({"version":AI_REPORT_NUMERIC_NORMALIZER_VERSION,"original":m.group(),"value":value,
                  "unit":_unit(m.groupdict().get("unit"),percent),"approximate":bool(m.groupdict().get("approx")),
                  "qualifier":m.groupdict().get("approx"),"start":m.start(),"end":m.end(),"kind":kind})
            except (ValueError,OverflowError):
                values.append({"version":AI_REPORT_NUMERIC_NORMALIZER_VERSION,"original":m.group(),"parsed":False,
                               "start":m.start(),"end":m.end(),"kind":kind})
    values.sort(key=lambda x:(x["start"],x["end"]))
    # Attach adjacent ranges without losing individual grounding.
    for left,right in zip(values,values[1:]):
        if RANGE_SEP_RE.fullmatch(text[left["end"]:right["start"]] or ""):
            left["range_end"]=right.get("value"); left["range_unit"]=right.get("unit") or left.get("unit")
    return values

def suspicious_unparsed(text:str)->list[str]:
    candidates=re.findall(rf"(?:百分之|约|接近|超过|低于|负)?[{CN_CHARS}廿卅]+",EXCLUDED.sub("",text))
    bad=[]
    for token in candidates:
        number=re.sub(r"^(?:百分之|约|接近|超过|低于|负)","",token)
        if number=="点" or (len(number)==1 and token==number):continue
        try:chinese_to_number(number)
        except ValueError:bad.append(token)
    return bad
