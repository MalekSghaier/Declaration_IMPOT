from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


# ATTENTION : les modeles envoyes a Mistral (chat.parse) ne doivent pas contenir de Decimal :
# son schema JSON contient une regex que Mistral refuse (erreur 400, code 3051).
# On utilise float pour le LLM, et on reconvertit en Decimal cote application.


class TvaLine(BaseModel):
    taux: float
    base_ht: float
    montant_tva: float


class InvoiceExtraction(BaseModel):
    numero: str | None = None
    date_facture: date | None = None
    emetteur_nom: str | None = None
    emetteur_mf: str | None = None
    client_nom: str | None = None
    client_mf: str | None = None
    lignes_tva: list[TvaLine] = []
    total_ht: float | None = None
    total_tva: float | None = None
    timbre: float | None = None
    total_ttc: float | None = None
    devise: str | None = None


class PatenteExtraction(BaseModel):
    """Champs extraits d'une carte d'identification fiscale tunisienne (patente)."""

    raison_sociale: str | None = Field(
        None,
        description=(
            "Valeur à côté de 'Nom et prénom ou raison sociale' (ou 'الاسم واللقب أو التسمية'). "
            "Pour une personne physique : Prénom + Nom. "
            "Pour une société : raison sociale en majuscules. "
            "Le champ peut être vide sur certaines patentes : dans ce cas, mets null."
        ),
    )
    matricule_fiscal: str | None = Field(
        None,
        description=(
            "Valeur à côté de 'Matricule Fiscal' (ou 'المعرف الجبائي'). "
            "Format standard : 7 chiffres + 1 lettre majuscule (ex: 1943273B) "
            "ou 7 chiffres + '/' + lettres (ex: 1234567/A/M/000). "
            "Recopie-le TEL QUEL avec ses lettres."
        ),
    )
    code_tva: str | None = Field(
        None,
        description=(
            "UNE SEULE LETTRE MAJUSCULE, valeur à côté de 'Code TVA' "
            "(ou 'رمز أقرع' dans la version arabe). "
            "Généralement 'A', 'B', 'C', 'D' ou 'N'. "
            "Ne pas confondre avec 'Code Catégorie' (qui est une autre colonne du même tableau)."
        ),
    )
    code_categorie: str | None = Field(
        None,
        description=(
            "UNE SEULE LETTRE MAJUSCULE, valeur à côté de 'Code Catégorie' "
            "(ou 'رمز الصنف' dans la version arabe). "
            "Généralement 'M', 'C', 'F', etc. "
            "Ne pas confondre avec 'Code TVA'."
        ),
    )
    adresse: str | None = Field(
        None,
        description=(
            "Valeur à côté de 'Adresse' (ou 'العنوان'). "
            "Inclut le numéro de voie, la rue, la ville, le code postal. "
            "Une seule adresse complète, ou null si absente."
        ),
    )
    activite: str | None = Field(
        None,
        description=(
            "Valeur à côté de 'Activité principal' (ou 'النشاط الرئيسي'). "
            "Ex: 'Programmation informatique', 'Commerce de détail', 'Restauration'. "
            "Ignore les 'Activités secondaires'."
        ),
    )


class RNEExtraction(BaseModel):
    """Champs extraits d'un extrait RNE (Registre National des Entreprises) tunisien.

    Le RNE est émis par un organisme unique avec un template standardisé :
    - page 1 : identification (tous les champs métier)
    - page 2 : direction + mentions + commissaire aux comptes
    - page 3 : mentions légales (inutile)
    """

    raison_sociale: str | None = Field(
        None,
        description=(
            "Valeur à côté de 'Dénomination sociale:' (pour une société) "
            "ou 'Nom et prénom' / 'الاسم واللقب' (pour une entreprise individuelle). "
            "Recopiée telle qu'écrite dans le document."
        ),
    )
    matricule_fiscal: str | None = Field(
        None,
        description=(
            "Valeur à côté de 'IDENTIFIANT UNIQUE' (ou 'المعرف الوحيد'). "
            "Format standard : 7 chiffres + 1 lettre majuscule (ex: 1943273B) "
            "ou 7 chiffres + '/' + lettres (ex: 1234567/A/M/000). "
            "NE PAS confondre avec : "
            "'NUMÉRO EXTRAIT' (format ER + chiffres), "
            "'N° DE GESTION INTERNE' (format B + chiffres), "
            "'R.C' ou 'RC' (registre de commerce)."
        ),
    )
    forme_juridique: str | None = Field(
        None,
        description=(
            "Valeur à côté de 'Forme juridique:' ou 'الشكل القانوني'. "
            "Réduis 'Société à Responsabilité Limitée (SARL)' à 'SARL'. "
            "Valeurs attendues : SARL, SA, SUARL, SNC, SCS, SAS, SCA. "
            "Laisse null pour une ENTREPRISE INDIVIDUELLE (pas de forme juridique)."
        ),
    )
    capital: float | None = Field(
        None,
        description=(
            "Valeur à côté de 'Capital social:' (ou 'رأس المال'). "
            "Nombre seul, sans 'DT' ni séparateur de milliers. Ex: 150000, pas '150 000 DT'. "
            "Laisse null pour une ENTREPRISE INDIVIDUELLE."
        ),
    )
    date_creation: date | None = Field(
        None,
        description=(
            "Date au format YYYY-MM-DD. "
            "Priorité 1 : 'Date de constitution' ou 'DATE D'IMMATRICULATION' si présentes. "
            "Priorité 2 : 'Date de publication' (تاريخ الشهار). "
            "JAMAIS 'DATE D'ÉDITION DE L'EXTRAIT' (تاريخ إستخراج المضمون) : "
            "c'est la date d'impression de l'extrait, elle change à chaque génération."
        ),
    )
    dirigeant: str | None = Field(
        None,
        description=(
            "Première personne du tableau 'INFORMATIONS RELATIVES À LA DIRECTION' "
            "(بيانات تخص الإدارة). "
            "Qualités attendues : gérant (وكيل), président (رئيس), directeur, exploitant. "
            "IGNORE les commissaires aux comptes. "
            "Le nom peut être écrit UNIQUEMENT en arabe dans le document. Dans ce cas, "
            "translittère-le en caractères latins selon la prononciation tunisienne "
            "(ex: صفوان قبّص → 'Safouane kais'). "
            "Si tu ne peux pas translittérer de façon raisonnable, mets null."
        ),
    )
    adresse: str | None = Field(
        None,
        description=(
            "Valeur à côté de 'Adresse du siège social:' (عنوان المقر الاجتماعي). "
            "PIÈGE SYSTÉMATIQUE : la ligne commençant par 'N°1' suivie de "
            "'Rue LAC TOBA les Berges du Lac' est l'adresse du RNE lui-même "
            "(organisme émetteur), répétée en pied de page de CHAQUE page. "
            "Elle ne concerne JAMAIS la société. IGNORE-LA. "
            "Ne prends pas non plus 'Adresse Activité' si elle diffère du siège. "
            "Ne concatène JAMAIS plusieurs adresses. Une seule valeur."
        ),
    )


class CompanyProfile(BaseModel):
    raison_sociale: str | None = None
    matricule_fiscal: str | None = None
    adresse: str | None = None
    forme_juridique: str | None = None
    capital: Decimal | None = None
    date_creation: date | None = None
    activite: str | None = None
    dirigeant: str | None = None
    code_tva: str | None = None
    code_categorie: str | None = None