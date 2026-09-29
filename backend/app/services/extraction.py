"""Comprehension du texte (LLM). Le texte peut venir du natif (PyMuPDF) ou de l'OCR."""
import re
import threading

from mistralai.client import Mistral
from pydantic import BaseModel

from app.config import settings
from app.services.document_reader import PageText
from app.services.schemas import InvoiceExtraction, PatenteExtraction, RNEExtraction, PayslipExtraction

_client_singleton: Mistral | None = None
_client_lock = threading.Lock()


def _client() -> Mistral:
    global _client_singleton
    if _client_singleton is None:
        with _client_lock:
            if _client_singleton is None:
                _client_singleton = Mistral(
                    api_key=settings.MISTRAL_API_KEY.get_secret_value(),
                    server_url=settings.MISTRAL_BASE_URL,
                )
    return _client_singleton


# ---------------------------------------------------------------------------
# Nettoyage du texte avant envoi au LLM
# ---------------------------------------------------------------------------
# Les documents RNE sont emis par un organisme unique avec un pied de page
# standardise qui revient sur CHAQUE page. Ce bruit pollue le LLM et peut etre
# confondu avec les donnees metier (ex: adresse de l'organisme prise pour
# l'adresse du siege). On le retire.
#
# IMPORTANT : on NE retire PAS l'arabe. Certaines donnees (ex: nom du dirigeant)
# peuvent n'exister qu'en version arabe. Le LLM Mistral gere le bilingue.

_NOISE_PATTERNS = [
    # Coordonnees de l'organisme emetteur (RNE / structure fiscale)
    r"registre-entreprises\.tn[^\s]*",
    r"https?://\S+",
    r"contact@registre-entreprises\.tn",
    r"www\.registre-entreprises\.tn",
    r"\+216[\s\d]+",
    # Adresse du RNE en pied de page (version francaise et arabe)
    r"N°1\s+Rue\s+LAC\s+TOBA[^\n]*",
    r"صافي\s+1\s+عدد\s+LAC\s+TOBA[^\n]*",
    # Lignes purement decoratives (QR codes vides, separateurs)
    r"\[\s*\]\([^)]+\)",
]


def _strip_noise(text: str) -> str:
    for pat in _NOISE_PATTERNS:
        text = re.sub(pat, " ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def pages_to_text(pages: list[PageText]) -> str:
    joined = "\n\n".join(f"--- Page {p.page + 1} ---\n{p.text}" for p in pages)
    return _strip_noise(joined)


def _extract(pages: list[PageText], schema: type[BaseModel], system: str) -> BaseModel:
    resp = _client().chat.parse(
        model=settings.MISTRAL_LLM_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": pages_to_text(pages)},
        ],
        response_format=schema,
        temperature=0,
    )
    return resp.choices[0].message.parsed


# ---------------------------------------------------------------------------
# Prompts systeme
# ---------------------------------------------------------------------------
# Tous les RNE sont emis par le meme organisme, tous les patentes aussi.
# On cible donc les libelles francais qui sont constants sur tous les documents,
# ainsi que les pieges qui sont constants eux aussi.

INVOICE_SYSTEM = """Tu extrais les donnees d'une facture tunisienne a partir de son texte (markdown).
Regles :
- Reponds uniquement selon le schema fourni.
- Montants en nombres avec point decimal (1234.500), sans separateur de milliers.
- Dates au format AAAA-MM-JJ.
- Si une information est absente ou illisible, mets null. N'invente jamais de valeur.
- Le matricule fiscal est recopie tel quel.
- Une ligne dans lignes_tva par taux de TVA distinct.
- Distingue bien l'emetteur (vendeur) du client."""

PATENTE_SYSTEM = """Tu extrais les informations d'une carte d'identification fiscale tunisienne (patente).

CONTEXTE
Toutes les patentes sont emises par la Direction Generale des Impots (Ministere des Finances)
avec un template STANDARDISE. Tu peux te fier a la structure, pas a des valeurs specifiques.
Le document est bilingue francais/arabe : les memes informations figurent dans les deux langues.
Utilise la version francaise quand elle existe ; l'arabe uniquement quand c'est la seule source.

STRUCTURE DU DOCUMENT (du haut vers le bas)
1. En-tete administratif : Republique Tunisienne, Ministere des Finances,
   Structure de Controle des Impots. A IGNORER.
2. Titre : "Carte d'Identification Fiscale" / "بطاقة التعريف الجبائية".
3. Mention de regime : "Soumis au regime reel", "Assujetti obligatoire pour toute l'activite".
4. Tableau a 4 colonnes (dans cet ordre, souvent) :
   - N° etablissement secondaire  /  رقم الفرع الثانوي
   - Code Categorie               /  رمز الصنف
   - Code TVA                     /  رمز الاداء  (parfois orthographie differemment en OCR)
   - Matricule Fiscal             /  المعرف الجبائي  (parfois : المعرف الحالي)
5. Bloc identification : Nom et prenom ou raison sociale / Adresse / Activite principal / A compter du.
6. Bloc activites secondaires (tableau vide ou rempli).
7. Signatures administratives (Chef de Bureau, Chef de Structure...). A IGNORER.
8. Pied de page : mentions legales "NB : ...". A IGNORER.

REGLES D'EXTRACTION PAR CHAMP

- matricule_fiscal
  * C'est la valeur de la colonne "Matricule Fiscal" du tableau en haut.
  * Format : une chaine alphanumerique, souvent 7 chiffres suivis d'UNE lettre majuscule,
    ou bien 7 chiffres + "/" + une lettre + "/" + une lettre + "/" + 3 chiffres.
    Le format peut varier : recopie EXACTEMENT la valeur telle qu'elle apparait.
  * Ne JAMAIS prendre la valeur de "N° etablissement secondaire" (numerique pur, souvent 000).
  * Ne JAMAIS prendre la valeur de "Code TVA" ni "Code Categorie" (lettres isolees).

- code_tva
  * Valeur de la colonne "Code TVA" du tableau en haut.
  * C'est UNE SEULE lettre majuscule (l'alphabet est limite, mais tu ne dois PAS
    presumer laquelle : extrait celle qui figure reellement dans le document).
  * Si la colonne est vide, mets null.

- code_categorie
  * Valeur de la colonne "Code Categorie" du tableau en haut.
  * C'est UNE SEULE lettre majuscule (different de Code TVA en general, mais pas toujours).
    Ne presume rien : recopie la lettre reellement presente.
  * Si la colonne est vide, mets null.

- raison_sociale
  * Valeur de la ligne "Nom et prenom ou raison sociale".
  * Peut etre :
      - une personne morale en majuscules (ex: plusieurs mots en capitales),
      - une personne physique (Prenom + Nom en casse normale),
      - VIDE sur certaines patentes. Dans ce cas, mets null.
  * Recopie telle quelle, sans reformatage.

- adresse
  * Valeur de la ligne "Adresse".
  * Sur le document, l'adresse s'etale souvent sur 2 a 3 lignes consecutives.
    Reconstitue-la en UNE SEULE chaine separee par des virgules.
  * Inclut le numero de voie, la rue, la ville, le code postal s'il est present.
  * Si plusieurs adresses apparaissent (siege + etablissement secondaire), garde
    celle qui est dans le bloc principal "Adresse" (pas les activites secondaires).

- activite
  * Valeur de la ligne "Activite principal".
  * C'est une description textuelle du metier (ex: domaine d'activite).
  * Ignore la ligne "Description" si elle contient du texte technique different.
  * Ignore "Activites secondaires".

PIEGES A EVITER
- Ne confonds pas les 4 colonnes du tableau du haut entre elles.
  Chacune a un role distinct : un numero, deux lettres de code, un identifiant alphanumerique.
- Ne te fie PAS a l'ordre visuel des cellules en OCR : sur les documents bilingues,
  la disposition en colonnes peut etre deformee. Repère chaque valeur par son LIBELLE (FR ou AR)
  plutot que par sa position.
- Ne concatene jamais deux champs differents dans une meme valeur.
- Le cachet bleu en bas de page et les zones arabes deformees par l'OCR ne contiennent
  aucune donnee metier utile.

REGLES FINALES
- Reponds uniquement selon le schema fourni.
- N'invente jamais de valeur. Si tu n'es pas certain a 90%, mets null.
- Preserve la casse et l'orthographe exacte des valeurs extraites."""



RNE_SYSTEM = """Tu extrais les informations d'un extrait RNE (Registre National des Entreprises) tunisien.

CONTEXTE
Le RNE est emis par un organisme unique (Registre National des Entreprises) avec un template
STANDARDISE. Tous les extraits partagent la meme structure, quels que soient la societe, la ville,
le type de registre (SOCIETE ou ENTREPRISE INDIVIDUELLE). Tu peux te fier a la structure,
PAS a des valeurs specifiques.

Le document est bilingue francais/arabe : les memes informations figurent dans les deux langues,
cote a cote ou en colonnes paralleles. Utilise la version francaise quand elle existe ;
l'arabe uniquement quand c'est la SEULE source (cas typique : nom du dirigeant en arabe).

STRUCTURE STANDARD (page 1 = identification, page 2 = direction + mentions, page 3 = mentions legales)

1. EN-TETE (en haut de chaque page) - A IGNORER ENTIEREMENT :
   - Code de verification du registre (chaine alphanumerique isolee)
   - QR code et image de sceau
   - Lien de validation, coordonnees de l'organisme
   - Blason "REPUBLIQUE TUNISIENNE / PRESIDENCE DU GOUVERNEMENT / RNE"

2. BANDEAU D'EN-TETE (sous le blason) :
   - "NUMERO EXTRAIT" / "عدد المضمون" : un identifiant qui commence souvent par un prefixe
     alphabetique suivi de chiffres. Ce n'est PAS le matricule fiscal.
   - "DATE D'EDITION DE L'EXTRAIT" / "تاريخ استخراج المضمون" : une date au format AAAA/MM/JJ.
     Ce n'est PAS une date de creation de la societe.
   - "IDENTIFIANT UNIQUE" / "المعرف الوحيد" : le matricule fiscal de la societe.
     C'est cette valeur qu'on veut dans matricule_fiscal.

3. TITRE : "EXTRAIT RNE (SOCIETE)" ou "EXTRAIT RNE (ENTREPRISE INDIVIDUELLE)".
   Ce titre determine la nature juridique (voir plus bas).

4. SECTION "IDENTIFICATION DE L'ENTREPRISE" / "تعريف المؤسسة" :
   C'est le bloc principal, contient la majorite des champs metier.
   Les libelles usuels (FR / AR) :
   - "Type de registre" / "نوع السجل"
   - "N° de gestion interne" / "عدد التصرف الداخلي"
   - "Identifiant unique" / "المعرف الوحيد"       -> matricule_fiscal
   - "Denomination sociale" / "الاسم الاجتماعي"   -> raison_sociale
   - "Nom commercial" / "الاسم التجاري"           -> a IGNORER
   - "Enseigne" / "الشارة"                        -> a IGNORER
   - "Adresse du siege social" / "المقر الاجتماعي" -> adresse
   - "Adresse Activite" / "عنوان النشاط"          -> a IGNORER (adresse d'exploitation, pas siege)
   - "Forme juridique" / "الشكل القانوني"         -> forme_juridique
   - "Capital social" / "رأس المال"               -> capital
   - "Duree de l'entreprise" / "مدة الشركة"       -> a IGNORER
   - "Date de publication" / "تاريخ الاشهار"      -> date_creation (fallback)

5. SECTION "INFORMATIONS RELATIVES A L'ACTIVITE" / "بيانات تخص النشاط" :
   Contient des details d'activite. A IGNORER pour notre extraction (sauf si tu veux
   explicitement ce champ). Nous ne recuperons pas l'activite via le RNE.

6. SECTION "INFORMATIONS RELATIVES A LA DIRECTION" / "بيانات تخص الادارة" :
   Tableau a plusieurs colonnes : nom, nationalite, qualite.
   Le "dirigeant" est la personne dont la qualite est gerant / president / directeur /
   exploitant. IGNORE les commissaires aux comptes (section separee "COMMISSAIRE AUX COMPTES").

7. PIED DE PAGE de chaque page : coordonnees postales de l'organisme RNE, telephones,
   sites web, mentions legales longues. A IGNORER ENTIEREMENT.

REGLES D'EXTRACTION PAR CHAMP

- raison_sociale
  * Cherche la valeur associee au libelle "Denomination sociale" (ou "الاسم الاجتماعي").
  * Pour une ENTREPRISE INDIVIDUELLE, ce libelle peut etre remplace par "Nom et prenom".
  * Peut etre :
      - une personne morale (majuscules, parfois abregee)
      - une personne physique (Prenom + Nom en casse normale)
  * Recopie telle quelle, sans reformatage.

- matricule_fiscal
  * Cherche la valeur associee au libelle "IDENTIFIANT UNIQUE" (ou "المعرف الوحيد").
    Ce libelle apparait DEUX fois dans le document :
      - une fois dans le bandeau d'en-tete,
      - une fois dans la section "IDENTIFICATION DE L'ENTREPRISE".
    Les deux valeurs doivent etre identiques. Si elles diffèrent, prends celle
    de la section identification (source principale).
  * Format : chaine alphanumerique de longueur variable. Peut etre :
      - 7 chiffres + 1 lettre majuscule
      - 7 chiffres + "/" + 1 lettre + "/" + 1 lettre + "/" + 3 chiffres
      - ou une autre variante. NE PRESUME PAS du format : recopie exactement.
  * NE PAS confondre avec :
      - "NUMERO EXTRAIT" (identifiant different, plus long, commence souvent par des lettres)
      - "N° DE GESTION INTERNE" (identifiant interne, commence souvent par une lettre)
      - "R.C" ou "RC" (numero de registre de commerce)
      - Les codes alphanumeriques isoles dans l'en-tete

- forme_juridique
  * Cherche la valeur associee a "Forme juridique" (ou "الشكل القانوني").
  * Le document peut donner la forme en toutes lettres (ex: "Societe a Responsabilite
    Limitee (SARL)") ou directement en abreviation. Normalise en abreviation usuelle :
      - "Societe a Responsabilite Limitee" ou "SARL"       -> "SARL"
      - "Societe Anonyme" ou "SA"                          -> "SA"
      - "Societe Unipersonnelle a Responsabilite Limitee"  -> "SUARL"
      - "Societe en Nom Collectif" ou "SNC"                -> "SNC"
      - "Societe en Commandite Simple" ou "SCS"            -> "SCS"
      - "Societe par Actions Simplifiee" ou "SAS"          -> "SAS"
      - "Societe en Commandite par Actions" ou "SCA"       -> "SCA"
    Si la forme n'est pas dans cette liste, garde la valeur telle quelle.
  * Pour une ENTREPRISE INDIVIDUELLE : mets null (pas de forme juridique).

- capital
  * Cherche la valeur associee a "Capital social" (ou "رأس المال").
  * Extrait UNIQUEMENT le nombre (chiffres), sans devise, sans espaces, sans virgule
    de milliers. Le document peut utiliser l'espace ou la virgule comme separateur,
    enleve-les.
  * Pour une ENTREPRISE INDIVIDUELLE : mets null.

- date_creation
  * Priorite 1 : si le document contient une date de constitution / immatriculation
    explicite, utilise-la.
  * Priorite 2 : utilise "Date de publication" (ou "تاريخ الاشهار").
  * Format de sortie : AAAA-MM-JJ.
  * NE JAMAIS utiliser la date d'edition de l'extrait (bandeau d'en-tete) : c'est la date
    a laquelle le document a ete imprime, elle change a chaque generation.

- dirigeant
  * Cherche dans le tableau "INFORMATIONS RELATIVES A LA DIRECTION" (ou "بيانات تخص الادارة").
  * Ce tableau a plusieurs colonnes ; la colonne nom peut etre a gauche ou a droite selon
    la version arabe/francaise. Repere par le libelle "NOM ET PRENOM" / "الاسم واللقب".
  * La colonne "QUALITE" / "الصفة" indique le role. Retiens la premiere personne dont la
    qualite est gerant / president / directeur / exploitant.
  * IGNORE les personnes listees dans la section "COMMISSAIRE AUX COMPTES" (section separee).
  * Si le nom est ecrit uniquement en arabe, translittere-le en caracteres latins selon
    la prononciation tunisienne usuelle. Si tu ne peux pas translitterer de facon
    raisonnable, mets null.
  * Si plusieurs dirigeants ont le meme role (ex: deux co-gerants), garde UNIQUEMENT le premier.

- adresse
  * Cherche la valeur associee a "Adresse du siege social" (ou "المقر الاجتماعي").
  * Il existe PLUSIEURS adresses dans le document :
      - l'adresse du siege social (a extraire)
      - l'adresse d'activite (a IGNORER)
      - l'adresse de l'organisme emetteur, repetee en pied de page de CHAQUE page (a IGNORER)
  * Repere l'adresse de l'organisme emetteur : elle est TOUJOURS dans le pied de page,
    souvent accompagnee de telephones, fax, site web, email. Elle mentionne un
    emplacement administratif connu (un quartier administratif de la capitale) et ne
    correspond JAMAIS a la societe concernee. IGNORE-LA systematiquement.
  * Ne concatene JAMAIS plusieurs adresses en une seule valeur.
  * Si l'adresse du siege s'etale sur plusieurs lignes dans le document, reconstitue-la
    en une seule chaine, separee par des virgules.

REGLES FINALES
- Reponds uniquement selon le schema fourni.
- N'invente jamais de valeur. Si tu n'es pas certain a 90%, mets null.
- Preserve la casse et l'orthographe exacte des valeurs extraites."""





PAYSLIP_SYSTEM = """Tu extrais les informations d'un bulletin de paie tunisien a partir de son texte
(markdown, parfois issu d'un OCR imparfait sur un document scanne).

CONTEXTE IMPORTANT
Les bulletins de paie tunisiens N'ONT PAS de template unique : chaque societe ou logiciel
de paie utilise sa propre mise en page, ses propres codes numeriques (ex: '01', '4600', '8100'),
et parfois des libelles abreges. NE TE FIE JAMAIS a un code numerique ni a la position d'une
ligne : repere chaque valeur par son LIBELLE, en tolerant les abreviations et les variantes
d'ecriture (accents, majuscules, points).

FORMAT DES NOMBRES
Les montants sont ecrits avec une virgule comme separateur decimal (ex: '1 995,867' ou '2083,379').
Convertis-les en nombre avec un point decimal (1995.867). Retire les espaces de separation
des milliers. Un OCR imparfait peut deformer des chiffres (ex: '1,\\$yo' pour '9,18%') :
si une valeur est clairement illisible ou aberrante, mets null plutot que de deviner.

REGLES PAR CHAMP

- salarie_nom : le nom du SALARIE (personne employee), jamais le nom de la societe employeuse.
  Cherche 'Nom & Prenom', 'Nom et prenom', ou une ligne 'Mr'/'Mme' suivie d'un nom.

- mois : le mois ET l'annee auxquels le salaire se rapporte, jamais une date d'edition ou
  de paiement. Cherche 'Mois de', 'Mois :', ou 'Periode du ... au ...' (dans ce cas, prends
  le mois de la date de FIN). Reponds au format AAAA-MM.

- salaire_brut : la valeur en face de 'SALAIRE BRUT' (ou 'SAL.BRUT'). C'est un total deja
  calcule sur le document, ne le recalcule jamais toi-meme a partir des lignes de gains.

- cnss_salariale : le MONTANT (pas le taux en %) en face de 'RETENUE CNSS', 'CNSS' ou
  'RET.CNSS', dans la colonne des retenues. Si les deux (taux et montant) apparaissent sur
  la meme ligne, prends le montant en dinars, jamais le pourcentage.

- salaire_imposable : la valeur en face de 'SALAIRE IMPOSABLE' ou 'SAL.IMPOS.'.

- retenue_irpp : la valeur en face de 'IRPP', 'I.U.', 'I.UNIQ.' ou 'IMPOT SUR LE REVENU'.

- css : la valeur en face de 'C.S.S', 'CSS' ou 'CONTRIBUTION S.S' (contribution sociale de
  solidarite, DIFFERENTE de la CNSS). Si cette ligne n'existe pas sur le document, mets null,
  ne l'invente jamais.

- net_a_payer : le montant final verse au salarie. Priorite 1 : la ligne 'NET A PAYER'.
  Priorite 2, uniquement si 'NET A PAYER' est absente : 'SALAIRE NET'. Si les deux lignes
  existent avec des valeurs differentes (primes ajoutees apres impot, par exemple), prends
  toujours 'NET A PAYER', qui est le dernier total du document.

REGLES FINALES
- Reponds uniquement selon le schema fourni.
- N'invente jamais de valeur. Si un champ n'est pas present ou illisible, mets null.
- Si le document fourni n'est manifestement pas un bulletin de paie, mets tous les champs a null."""

def extract_invoice(pages: list[PageText]) -> InvoiceExtraction:
    return _extract(pages, InvoiceExtraction, INVOICE_SYSTEM)


def extract_patente(pages: list[PageText]) -> PatenteExtraction:
    return _extract(pages, PatenteExtraction, PATENTE_SYSTEM)


def extract_rne(pages: list[PageText]) -> RNEExtraction:
    # Page 3 = mentions legales, on la garde si elle existe mais le LLM saura l'ignorer.
    return _extract(pages, RNEExtraction, RNE_SYSTEM)


def extract_payslip(pages: list[PageText]) -> PayslipExtraction:
    return _extract(pages, PayslipExtraction, PAYSLIP_SYSTEM)