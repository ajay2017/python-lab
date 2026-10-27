from stock_analyzer.score_label import score_phrase


def test_pick_with_composite():
    assert score_phrase({"score": 100, "momentum_score": 100.0, "composite_score": 71.2}) == "momentum 100 · composite 71"


def test_pick_composite_none():
    assert score_phrase({"score": 94, "momentum_score": 94.0, "composite_score": None}) == "momentum 94"


def test_add_row_matches_old_fstring():
    b = {"score": 73.4}
    assert score_phrase(b) == f"Score {b.get('score', 0):.0f}" == "Score 73"


def test_g20_row_momentum_none_keeps_score():
    assert score_phrase({"score": 62, "momentum_score": None}) == "Score 62"


def test_bool_or_nan_momentum_falls_back():
    assert score_phrase({"score": 55, "momentum_score": True}) == "Score 55"
    assert score_phrase({"score": 55, "momentum_score": float("nan")}) == "Score 55"


def test_non_numeric_score_is_zero():
    assert score_phrase({"score": "n/a"}) == "Score 0"
    assert score_phrase({}) == "Score 0"


def test_real_shaped_rows():
    macro = {"ticker": "SHOP", "sector": "Consumer Tech", "score": 100.0,
             "momentum_score": 100.0, "composite_score": None, "reason": "x"}
    add = {"ticker": "NVDA", "sector": "Technology", "score": 73.0, "reason": "x"}
    assert score_phrase(macro) == "momentum 100"
    assert score_phrase(add) == "Score 73"
