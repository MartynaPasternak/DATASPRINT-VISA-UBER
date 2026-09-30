# Brand normalisation for Polish restaurants and fast food (MCC 5812 EATING PLACES AND RESTAURANTS, 5814 FAST FOOD).
# Used by restaurants_pl_model.py. Input is a merchant name already cleaned by the SQL macros name_norm + strip_tills
# (upper case, no accents, punctuation -> spaces, till suffixes removed).
#
# Rule, in order:
#   1. Vending machines (AUTOMATY ..., DELIKOMAT, ...VENDING) are not restaurants: brand = None, row dropped.
#   2. Clean base: drop leading facilitator prefixes (PL, SUMUP, SQ...), number tokens (shop numbers, terminal IDs,
#      also when glued to a word: "HANOI52790"), legal forms (SP Z O O, S C...), business-type codes (FHU, PPHU...),
#      one-letter tokens and the merchant's city name.
#   3. Known national chain (curated list below, checked against the most frequent names in the data) -> canonical
#      chain name, also when it follows a generic word ("RESTAURACJA SPHINX", "PIZZERIA DA GRASSO") or an operator code
#      ("PL KFC KRAKOW BONARKA", "PL PH ...", "PL BK ...", "PL SBX ...").
#   4. A base made only of generic words ("RESTAURACJA", "BAR MLECZNY", "MALA GASTRONOMIA", a first name) says nothing
#      about the owner: the merchant is its own brand (brand = "#" + merchant key), so unrelated places never merge.
#   5. Otherwise brand = the clean base: names identical apart from numbers, city and legal form are one brand
#      ("AM AM KEBAB 79239" = "AM AM KEBAB LUBLIN" = "AM AM KEBAB").
import re

PREFIX = {"PL", "POL", "SUMUP", "SQ", "ZETTLE", "IZ", "PAYU", "PAYPRO", "STRIPE", "PAYPAL", "TPAY", "DOTPAY", "SRV", "SP", "C"}
BIZ_CODES = {"FHU", "PHU", "PPHU", "FPHU", "FUH", "FH", "PW", "PUH", "FPUH", "PPUH", "ZPHU", "PHUP", "PPH", "PU", "FU", "PHP",
             "FHUP", "PPHUP", "FHG", "ZPH", "PPUH", "PTH"}
LEGAL_RE = re.compile(r"\b(SPOLKA|SPOL|SP)( (Z|ZO|ZOO|O|OO|J|K|KOM|KA|C|A|OGR|OGRANICZONA|ODPOWIEDZIALNOSCIA|CYWILNA|JAWNA|"
                      r"KOMANDYTOWA|AKCYJNA))*\b|\bSPZOO\b|\bS (C|A|J|K)\b")
NONSHOP_RE = re.compile(r"\b(AUTOMAT[A-Z]*|[A-Z]*VENDING[A-Z]*|DELIKOMAT|DELEKTOMAT[A-Z]*|VEMAT|SELL ?MATIC|KAWOMAT|"
                        r"[A-Z]+OMATY?)\b")

GENERIC = set("""
RESTAURACJA RESTAURACJE RESTAURANT RESTAURANTS RESTAURANTE RISTORANTE REST BAR BARY PUB PUBY BISTRO PIZZERIA PIZZERIE
PIZZA PIZZY KEBAB KEBABY KEBAP DONER KAWIARNIA KAWIARENKA KLUBOKAWIARNIA CAFE CAFFE CAFFEE COFFEE KAWA HERBACIARNIA
LODY LODZIARNIA LODZIARNIE GOFRY CUKIERNIA PIEKARNIA NALESNIKARNIA PIEROGARNIA PIEROGI PIWIARNIA PIJALNIA ZAPIEKARNIA
ZAJAZD KARCZMA GOSPODA GOSCINIEC OBERZA TAWERNA TRATTORIA OSTERIA JADLODAJNIA STOLOWKA KANTYNA BUFET SMAZALNIA
ZAPIEKANKI ZAPIEKANKA BURGER BURGERY BURGERS GRILL SUSHI RAMEN PHO KUCHNIA KUCHNIE FOODTRUCK
MALA GASTRONOMIA GASTRONOMICZNA GASTRONOMICZNE GASTRONOMICZNY GASTRO USLUGI USLUGA FIRMA HANDLOWO USLUGOWA
USLUGOWO HANDLOWA HANDLOWE HANDEL PRODUKCYJNO PRZEDSIEBIORSTWO ZAKLAD PUNKT LOKAL LOKALIZACJA CATERING CATERINGOWE
HOTEL HOSTEL MOTEL PENSJONAT KLUB CLUB SALA BANKIETOWA OSRODEK WYPOCZYNKOWY CENTRUM DOM SKLEP SKLEPIK SPOZYWCZY KIOSK
BUDKA FOOD FOODS TRUCK STREET FAST MLECZNY OBIADY DOMOWE DOMOWA DOMOWY POLSKA POLSKIE POLSKI SMAKI SMAK SMAKOW SMAKU
PRZYSMAKI KURCZAK KURCZE PIECZONE PIECZONY RYB RYBY RYBACKA NA POD PRZY DO PO ZA NAD OD LA LE EL IL DA DE DI DEL THE
AND OF AT MR SPOLDZIELNIA SOCJALNA FUNDACJA STOWARZYSZENIE PARAFIA SZKOLNY SZKOLNA STUDENCKA ORIENTALNY ORIENTALNA
CHINSKI CHINSKA WIETNAMSKI WIETNAMSKA WLOSKA TURECKI EXPRESS EKSPRES MINI MAXI SUPER PLUS NOWA NOWY STARA STARY
""".split())
FIRST_NAMES = set("""
ANNA MARIA KATARZYNA MALGORZATA AGNIESZKA BARBARA EWA KRZYSZTOF ANDRZEJ PIOTR TOMASZ PAWEL MICHAL MAREK JAN JOANNA
MAGDALENA MONIKA BEATA JOLANTA ELZBIETA ALEKSANDRA JUSTYNA DOROTA IWONA GRZEGORZ JAKUB LUKASZ ADAM ROBERT MARCIN
WOJCIECH RAFAL JACEK DARIUSZ ZBIGNIEW KAMIL MATEUSZ BARTOSZ DANUTA HALINA IRENA TERESA ZOFIA KRYSTYNA URSZULA RENATA
SYLWIA EDYTA KAROLINA NATALIA PATRYK DAWID DANIEL MACIEJ MARTA ARTUR DAMIAN MARIUSZ SEBASTIAN SLAWOMIR LESZEK HENRYK
JERZY RYSZARD STANISLAW JOZEF TADEUSZ KAZIMIERZ WLADYSLAW ZDZISLAW JANUSZ MIROSLAW WIESLAW WITOLD BOGDAN ROMAN
PRZEMYSLAW RADOSLAW SZYMON FILIP KACPER KRYSTIAN HUBERT OSKAR BARTLOMIEJ ADRIAN NORBERT KONRAD IGOR EMIL ARKADIUSZ
AGATA PAULINA WERONIKA JULIA ZUZANNA WIKTORIA KINGA ANETA IZABELA KLAUDIA MARZENA GRAZYNA BOZENA ALICJA MILENA EMILIA
OLGA LIDIA DOMINIKA ANGELIKA DIANA LUCYNA WANDA HANNA HELENA JADWIGA STEFANIA GABRIELA MARIOLA ILONA EWELINA MARLENA
ADRIANNA OLIWIA AMELIA NIKOLA LAURA MICHALINA KORNELIA ZANETA PATRYCJA VOLODYMYR OLEKSANDR OLENA TETIANA IRYNA
""".split())
# Truncated generic words ("GASTRONOMICZN", "RESTAURAC", "HANDLOWO US") are generic too: any 5+ letter prefix.
GENERIC_PREFIXES = {w[:k] for w in GENERIC for k in range(5, len(w) + 1)}

# Curated national chains: canonical name -> regex on the clean base (or on the base after leading generic words).
CHAINS = {
    "MCDONALDS": r"^MC ?DONALDS?\b|^MCD\b",
    "KFC": r"^KFC\b",
    "BURGER KING": r"^BURGER KING\b|^BK\b",
    "PIZZA HUT": r"^PIZZA HUT\b|^PH (EXPRESS|GALERIA|DELIVERY|DEL)\b",
    "STARBUCKS": r"^STARBUCKS\b|^SBX\b",
    "COSTA COFFEE": r"^COSTA (COFFEE|CAFFEE|CAFE)\b|^COSTA\b",
    "SUBWAY": r"^SUBWAY\b",
    "DOMINOS PIZZA": r"^DOMINOS\b",
    "TELEPIZZA": r"^TELEPIZZA\b",
    "PAPA JOHNS": r"^PAPA JOHNS?\b",
    "POPEYES": r"^POPEYES\b",
    "MAX BURGERS": r"^MAX (PREMIUM )?BURGERS\b",
    "NORTH FISH": r"^NORTH FISH\b",
    "SALAD STORY": r"^SALAD STORY\b",
    "SPHINX": r"^SPHINX\b",
    "OLIMP": r"^OLIMP\b",
    "DA GRASSO": r"^DA ?GRASSO\b",
    "BAFRA KEBAB": r"^BAFRA\b",
    "ZAHIR KEBAB": r"^ZAHIR KEBAB\b",
    "BERLIN DONER KEBAP": r"^BERLIN DONER\b",
    "AM AM KEBAB": r"^AM AM\b",
    "KEBAB U PAJDY": r"^PAJDY\b|^KEBAB PAJDY\b",
    "TENDUR KEBAP": r"^TENDUR\b",
    "LODOLANDIA": r"^LODOLANDIA\b",
    "KOLACZ NA OKRAGLO": r"^KOLACZ NA OKRAGLO\b",
    "GRYCAN": r"^GRYCAN\b|^LODZIARNIE FIRMOWE\b",
    "GREEN CAFFE NERO": r"^GREEN (CAFFE|COFFEE)( NERO)?\b",
    "SO COFFEE": r"^SO COFFEE\b",
    "CRAZY BUBBLE": r"^CRAZY BUBBLE\b",
    "KOKU SUSHI": r"^KOKU SUSHI\b",
    "NOVA SUSHI": r"^NOVA SUSHI\b",
    "SUSHI KUSHI": r"^SUSHI KUSHI\b",
    "SILVER DRAGON": r"^SILVER DRAGON\b",
    "KURCZAK Z ROZNA": r"^KURCZAK ROZNA\b",
    "1 MINUTE": r"^MINUTE\b",
    "INMEDIO": r"^INMEDIO\b",
    "PIJALNIA WODKI I PIWA": r"^PIJALNIA WODKI PIWA\b",
    "WEDEL PIJALNIA CZEKOLADY": r"^PIJALNIE? CZEKOLADY\b|^WEDEL\b",
    "GRUBY BENEK": r"^GRUBY BENEK\b",
    "PIJANA WISNIA": r"^PIJANA WISNIA\b",
    "MINISTERSTWO SLEDZIA": r"^MINISTERSTWO SLEDZIA\b",
    "FOOD CARGO": r"^FOOD CARGO\b",
    "MAQARON": r"^MAQARON\b",
    "MAKARUN": r"^MAKARUN\b",
    "KARMELLO": r"^KARMELLO\b",
    "DOMINIUM": r"^DOMINIUM\b",
    "TUTTI SANTI": r"^TUTTI SANTI\b",
    "BIKE CAFE": r"^BIKE CAFE\b",
    "THAI WOK": r"^THAI WOK\b",
    "PIZZERIA 105": r"^STOPIATKA\b|^PIZZERIA STOPIATKA\b",
    "PIWIARNIA WARKA": r"^PIWIARNIA WARKA\b",
    "IKEA": r"^IKEA\b",
    "PASIBUS": r"^PASIBUS\b",
    "BOBBY BURGER": r"^BOBBY BURGER\b",
    "BOBOQ": r"^BOBOQ\b",
    "HALLO PIZZA": r"^HALLO PIZZA\b",
    "CYBERMACHINA": r"^CYBERMACHINA\b",
    "KUCHNIA MARCHE": r"^KUCHNI[AE] MARCHE\b|^MARCHE\b",
    "SWIAT PIEROGOW": r"^SWIAT PIEROGOW\b",
    "PIEROGARNIA STARY MLYN": r"^PIEROGARNIA STARY MLYN\b|^STARY MLYN\b",
    "MANEKIN": r"^MANEKIN\b",
    "SODEXO": r"^SODEXO\b",
    "AMREST": r"^AMREST\b",
    "ORLEN": r"^ORLEN\b",
    "AMIC": r"^AMIC\b",
}
# Operator codes that carry the chain in the raw name: "PL KFC KRAKOW BONARKA", "PL PH OPOLE OPOLE", "PL SBX ...".
OPERATOR_PREFIX = {"PL KFC": "KFC", "PL PH": "PIZZA HUT", "PL BK": "BURGER KING", "PL SBX": "STARBUCKS"}
_CHAIN_RES = [(k, re.compile(v)) for k, v in CHAINS.items()]


def is_generic(tok: str) -> bool:
    return tok in GENERIC or tok in FIRST_NAMES or tok in GENERIC_PREFIXES


def is_vending(name: str) -> bool:
    return bool(NONSHOP_RE.search(name))


def clean_base(name: str, city: str = "") -> str:
    s = re.sub(r"([A-Z])([0-9])", r"\1 \2", name)
    s = re.sub(r"([0-9])([A-Z])", r"\1 \2", s)
    toks = s.split()
    while toks and toks[0] in PREFIX:
        toks = toks[1:]
    s = " ".join(t for t in toks if not t.isdigit())
    s = LEGAL_RE.sub(" ", s)
    city_toks = set((city or "").split())
    toks = [t for t in s.split() if len(t) > 1 and t not in BIZ_CODES and t not in city_toks]
    return " ".join(toks)


def chain_of(name: str, base: str) -> str | None:
    for pre, chain in OPERATOR_PREFIX.items():
        if name == pre or name.startswith(pre + " "):
            return chain
    # The base itself, then the base after each leading generic word ("PIZZERIA DA GRASSO" -> "DA GRASSO" -> "GRASSO").
    toks = base.split()
    cands = [base]
    while len(toks) > 1 and is_generic(toks[0]):
        toks = toks[1:]
        cands.append(" ".join(toks))
    for cand in cands:
        for chain, rx in _CHAIN_RES:
            if rx.search(cand):
                return chain
    return None


def to_brand(name: str, city: str, merchant_key: str) -> str | None:
    if is_vending(name):
        return None
    base = clean_base(name, city)
    chain = chain_of(name, base)
    if chain:
        return chain
    if not base or all(is_generic(t) for t in base.split()):
        return "#" + merchant_key
    return base


# Worked examples from the data (name, city, expected brand). "#" = the merchant is its own brand.
EXAMPLES = [
    ("MCDONALDS 010 KRAKOW", "KRAKOW", "MCDONALDS"),
    ("PL KFC KRAKOW BONARKA", "KRAKOW", "KFC"),
    ("PL PH WROCLAW ASTRA DEL", "WROCLAW", "PIZZA HUT"),
    ("PL SBX KATOWICE GALERIA", "KATOWICE", "STARBUCKS"),
    ("BK GDANSK AK", "GDANSK", "BURGER KING"),
    ("RESTAURACJA SPHINX GDYNIA", "GDYNIA", "SPHINX"),
    ("PIZZERIA DA GRASSO", "LODZ", "DA GRASSO"),
    ("GRYCAN LODZIARNIE FIRM", "WARSZAWA", "GRYCAN"),
    ("LODZIARNIE FIRMOWE", "WARSZAWA", "GRYCAN"),
    ("BAFRA KEBAB MPK 300", "POZNAN", "BAFRA KEBAB"),
    ("BAFRA KEBAB ST005", "LODZ", "BAFRA KEBAB"),
    ("1 MINUTE 54602", "GDANSK", "1 MINUTE"),
    ("AM AM KEBAB 79239", "LUBLIN", "AM AM KEBAB"),
    ("AM AM KEBAB LUBLIN", "LUBLIN", "AM AM KEBAB"),
    ("KEBAB U PAJDY GLIWICE", "GLIWICE", "KEBAB U PAJDY"),
    ("RESTAURACJA HANOI52790", "OPOLE", "RESTAURACJA HANOI"),
    ("PIZZERIA ROMA 2", "PLOCK", "PIZZERIA ROMA"),
    ("THE KING KEBAB KCYNIA", "KCYNIA", "THE KING KEBAB"),
    ("RESTAURACJA", "KRAKOW", "#m"),
    ("BAR MLECZNY", "WARSZAWA", "#m"),
    ("MALA GASTRONOMIA", "ZORY", "#m"),
    ("USLUGI GASTRONOMICZN 01", "RADOM", "#m"),
    ("FIRMA HANDLOWO USLUG 06", "RADOM", "#m"),
    ("ANNA", "RADOM", "#m"),
    ("ANNA SZOLTYSIK", "RYBNIK", "ANNA SZOLTYSIK"),
    ("FHU KAWIARNIA SLONECZ44103", "OPOLE", "KAWIARNIA SLONECZ"),
    ("GREEN COFFEE SP Z O O", "WARSZAWA", "GREEN CAFFE NERO"),
    ("PICCOLO SP ZO O", "POZNAN", "PICCOLO"),
    ("AUTOMATY AS VENDING", "ZORY", None),
    ("C C DELIKOMAT PL 18676", "LODZ", None),
    ("AUTOMAT SPEC", "LISZKI", None),
]


def _self_test() -> None:
    for name, city, want in EXAMPLES:
        got = to_brand(name, city, "m")
        assert got == want, (name, got, want)


_self_test()
