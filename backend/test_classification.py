from app.services.classification import classify_direction, normalize_mf


def sev(issues):
    return [(i.field, i.severity) for i in issues]


def test_normalisation():
    assert normalize_mf("1234567/A/M/000") == "1234567A"
    assert normalize_mf(" 1234567 a ") == "1234567A"
    assert normalize_mf("MF : 1234567A") == "1234567A"
    assert normalize_mf("12345") is None
    assert normalize_mf(None) is None


def test_vente_tolere_formats():
    d, issues = classify_direction("1234567/A/M/000", "7654321B", "1234567 a")
    assert d == "VENTE" and issues == []


def test_achat():
    d, issues = classify_direction("7654321B", "1234567A", "1234567A")
    assert d == "ACHAT" and issues == []


def test_aucun_ne_correspond():
    d, issues = classify_direction("7654321B", None, "1234567A")
    assert d is None and sev(issues) == [("direction", "warning")]


def test_les_deux():
    d, issues = classify_direction("1234567A", "1234567/A/M/000", "1234567A")
    assert d is None and sev(issues) == [("direction", "error")]


def test_matricule_societe_illisible():
    d, issues = classify_direction("1234567A", "7654321B", "abc")
    assert d is None and sev(issues) == [("direction", "warning")]