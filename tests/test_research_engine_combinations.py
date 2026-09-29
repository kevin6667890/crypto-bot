from research_engine.cross_asset import align_confirmed
from research_engine.indicators import combinations
def test_combination_recipes_are_bounded_and_three_components_maximum():
    values=combinations(); assert len(values)==20 and all(x['complexity']<=3 for x in values)
def test_cross_asset_alignment_never_fills_missing_or_unconfirmed_bars():
    primary=[{'ts':1,'confirmed':1},{'ts':2,'confirmed':1},{'ts':3,'confirmed':1}]
    context=[{'ts':1,'confirmed':1},{'ts':2,'confirmed':0},{'ts':4,'confirmed':1}]
    assert [(a['ts'],b['ts']) for a,b in align_confirmed(primary,context)]==[(1,1)]
