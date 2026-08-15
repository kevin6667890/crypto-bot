import pytest
from dashboard.ai_market_analysis.report_numeric_normalizer import chinese_to_number,normalize_numbers

@pytest.mark.parametrize("text,value",[("一千九百二十八",1928),("六点八",6.8),("十",10),("两千",2000),("一亿零三万",100030000)])
def test_chinese_numbers(text,value):assert chinese_to_number(text)==value

@pytest.mark.parametrize("text,values",[("百分之六点八",[6.8]),("一千八百八十五到一千八百九十二",[1885,1892]),("约一千九百",[1900]),("1,928",[1928]),("1 928",[1928]),("1.9k",[1900]),("1.2万",[12000]),("-6.8%",[-6.8]),("0.5 ATR",[.5])])
def test_supported_forms(text,values):assert [x["value"] for x in normalize_numbers(text)]==values

@pytest.mark.parametrize("text",["15分钟","1小时","2026-08-06","ai-report-v1","LEVEL_01","DATA_WARNING_01/02/03"])
def test_non_market_numbers_excluded(text):assert normalize_numbers(text)==[]


def test_shared_prefix_status_ids_do_not_hide_adjacent_market_values():
    text = "缺失 DATA_WARNING_01/02/03，价格区间 1860.58-1900.00 USDT。"
    assert [item["value"] for item in normalize_numbers(text)] == [1860.58, 1900.0]
