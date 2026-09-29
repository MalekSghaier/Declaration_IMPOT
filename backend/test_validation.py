from datetime import date

from app.services.schemas import InvoiceExtraction, TvaLine
from app.services.validation import validate_invoice


def make(**changes) -> InvoiceExtraction:
    data = dict(
        numero="143",
        date_facture=date(2025, 9, 15),
        emetteur_mf="1234567A",
        client_mf="7654321B",
        lignes_tva=[TvaLine(taux=19.0, base_ht=100.0, montant_tva=19.0)],
        total_ht=100.0,
        total_tva=19.0,
        timbre=1.0,
        total_ttc=120.0,
        devise="TND",
    )
    data.update(changes)
    return InvoiceExtraction(**data)


def found(issues, field, severity) -> bool:
    return any(i.field == field and i.severity == severity for i in issues)


def test_facture_valide():
    assert validate_invoice(make(), "2025-09") == []


def test_sans_timbre_pas_de_plantage():
    assert validate_invoice(make(timbre=None, total_ttc=119.0), "2025-09") == []


def test_total_ttc_incoherent():
    assert found(validate_invoice(make(total_ttc=125.0)), "total_ttc", "error")


def test_taux_invalide():
    inv = make(
        lignes_tva=[TvaLine(taux=20.0, base_ht=100.0, montant_tva=20.0)],
        total_tva=20.0,
        total_ttc=121.0,
    )
    assert found(validate_invoice(inv), "lignes_tva", "error")


def test_ligne_tva_incoherente():
    inv = make(
        lignes_tva=[TvaLine(taux=19.0, base_ht=100.0, montant_tva=25.0)],
        total_tva=25.0,
        total_ttc=126.0,
    )
    assert found(validate_invoice(inv), "lignes_tva", "warning")


def test_plusieurs_taux():
    inv = make(
        lignes_tva=[
            TvaLine(taux=7.0, base_ht=100.0, montant_tva=7.0),
            TvaLine(taux=19.0, base_ht=200.0, montant_tva=38.0),
        ],
        total_ht=300.0,
        total_tva=45.0,
        total_ttc=346.0,
    )
    assert validate_invoice(inv, "2025-09") == []


def test_somme_des_lignes_differente_du_total_tva():
    assert found(validate_invoice(make(total_tva=30.0, total_ttc=131.0)), "total_tva", "error")


def test_devise_etrangere():
    assert found(validate_invoice(make(devise="EUR")), "devise", "warning")


def test_devise_dt_acceptee():
    assert validate_invoice(make(devise="DT"), "2025-09") == []


def test_date_hors_periode():
    assert found(validate_invoice(make(), "2025-10"), "date_facture", "warning")
    assert not found(validate_invoice(make()), "date_facture", "warning")


def test_aucun_matricule():
    assert found(validate_invoice(make(emetteur_mf=None, client_mf=None)), "emetteur_mf", "warning")


def test_matricule_douteux():
    assert found(validate_invoice(make(emetteur_mf="12345")), "emetteur_mf", "warning")


def test_champs_manquants():
    issues = validate_invoice(make(numero=None, total_ttc=None))
    assert found(issues, "numero", "error")
    assert found(issues, "total_ttc", "error")


def test_avoir_montants_negatifs():
    inv = make(
        lignes_tva=[TvaLine(taux=19.0, base_ht=-100.0, montant_tva=-19.0)],
        total_ht=-100.0,
        total_tva=-19.0,
        timbre=None,
        total_ttc=-119.0,
    )
    assert validate_invoice(inv, "2025-09") == []