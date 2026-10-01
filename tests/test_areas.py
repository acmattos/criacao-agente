from aurora_assistant import areas


def test_lookup_taxa_known_area():
    assert areas.lookup_taxa("salao-de-festas") == 150.0
    assert areas.lookup_taxa("quadra") == 0


def test_lookup_taxa_unknown_area_returns_none():
    assert areas.lookup_taxa("area-que-nao-existe") is None
