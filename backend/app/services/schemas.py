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


class PayslipExtraction(BaseModel):
    """Champs extraits d'un bulletin de paie tunisien.

    Les bulletins tunisiens n'ont pas de template unique : codes numeriques,
    disposition et libellés abreges varient d'une societe a l'autre.
    L'extraction s'appuie sur le LIBELLE de chaque ligne, jamais sur sa position
    ou son code.
    """

    salarie_nom: str | None = Field(
        None,
        description=(
            "Nom et prenom du salarie (pas de la societe). "
            "Cherche 'Nom & Prenom', 'Nom et prenom', ou la ligne juste apres "
            "un champ 'Matricule'/'Mr'/'Mme'. "
            "IMPORTANT : si cette zone est noircie, barree, floutee ou autrement rendue "
            "illisible sur le document, mets null. Ne recopie JAMAIS le libelle d'un champ "
            "voisin (ex: 'FONCTION', 'SERVICE') a la place d'un nom absent."
        ),
    )
    mois: str | None = Field(
        None,
        description=(
            "Mois et annee concernes par le bulletin (PAS la date d'edition). "
            "Cherche 'Mois de', 'Mois :', ou une 'Periode du ... au ...' (dans ce cas "
            "prends le mois de la date de FIN de periode). "
            "Format de sortie : AAAA-MM (ex: 'novembre 2021' -> '2021-11')."
        ),
    )
    salaire_brut: float | None = Field(
        None,
        description=(
            "Valeur en face de 'SALAIRE BRUT' (ou 'SAL.BRUT'). "
            "C'est un sous-total, PAS la somme que tu dois recalculer toi-meme : "
            "prends la valeur telle qu'affichee sur le document."
        ),
    )
    cnss_salariale: float | None = Field(
        None,
        description=(
            "Montant (pas le taux) en face de 'RETENUE CNSS', 'CNSS' ou 'RET.CNSS' "
            "dans la colonne des retenues/deductions. "
            "Ne prends pas le taux en pourcentage (ex: '9,18%'), prends le montant en dinars."
        ),
    )
    salaire_imposable: float | None = Field(
        None,
        description="Valeur en face de 'SALAIRE IMPOSABLE' ou 'SAL.IMPOS.'.",
    )
    retenue_irpp: float | None = Field(
        None,
        description=(
            "Montant en face de 'IRPP', 'I.U.', 'I.UNIQ.', 'IMPOT SUR LE REVENU' "
            "ou 'Retenue IRPP'. C'est la retenue a la source sur salaire."
        ),
    )
    css: float | None = Field(
        None,
        description=(
            "Montant en face de 'C.S.S', 'CSS', 'CONTRIBUTION S.S' ou "
            "'Contribution sociale' (contribution sociale de solidarite, distincte de la CNSS). "
            "Absente sur certains bulletins : dans ce cas mets null."
        ),
    )
    net_a_payer: float | None = Field(
        None,
        description=(
            "Montant final verse au salarie. Priorite 1 : 'NET A PAYER'. "
            "Priorite 2, si 'NET A PAYER' est absent : 'SALAIRE NET'. "
            "Ces deux montants peuvent differer (primes ou avantages ajoutes apres impot) : "
            "prends toujours le dernier total en bas du document, pas un sous-total intermediaire."
        ),
    )