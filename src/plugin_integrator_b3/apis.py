"""What the plugin attaches to (plugin API 2.2, liminal_libs.sources.apis): B3's COTAHIST historical quotation files,
with the pages that publish and describe them and their layout. Every address below was read on B3's own pages on
2026-11-11: the historical quotes pages (Portuguese and English), the page of the files to download (yearly 1986 on,
monthly for the last twelve months, daily for the current year), the layout document (revision 02, 2020-10-05: the
record types and the code tables), the series' prices on CD-ROM and B3's terms of use. Nothing is asked to show it."""
from __future__ import annotations

from liminal_libs.sources.apis import Api, Area, Call, Link

VERIFIED = "2026-11-11"
NAME = "B3 COTAHIST historical quotes"
SERIES = "https://www.b3.com.br/pt_br/market-data-e-indices/servicos-de-dados/market-data/historico/mercado-a-vista"
QUOTES_PT = f"{SERIES}/cotacoes-historicas/"
FILES_PAGE = f"{SERIES}/series-historicas/"
QUOTES_EN = ("https://www.b3.com.br/en_us/market-data-and-indices/data-services/market-data/historical-data/equities/"
             "historical-quote-data/")
LAYOUT = "https://www.b3.com.br/data/files/33/67/B9/50/D84057102C784E47AC094EA8/SeriesHistoricas_Layout.pdf"
LAYOUT_EN = "https://www.b3.com.br/data/files/65/50/AD/26/29C8B51095EE46B5790D8AA8/HistoricalQuotations_B3.pdf"
PRICES = "https://bvmf.bmfbovespa.com.br/pt-br/cotacoes-historicas/FormSeriesHistoricasPrecos.asp"
TERMS = "https://www.b3.com.br/pt_br/termos-de-uso-e-protecao-de-dados/termos-de-uso/"
TERMS_EN = "https://www.b3.com.br/en_us/terms-of-use-and-data-protection/terms-of-use/"

YEARLY = "Yearly files"
MONTHLY = "Monthly files"
DAILY = "Daily files"
FRAME = "Header and trailer records"
CASH = "Cash market (TPMERC 010)"
ODD_LOT = "Odd-lot market (020)"
OPTIONS = "Options (070, 080) and their exercises (012, 013)"
FORWARD = "Term and forward markets (030, 050, 060)"
AUCTIONS = "Auctions (017)"
TABLES = "Code tables (CODBDI, ESPECI, INDOPC)"

COTAHIST = Api(
    name=NAME,
    maker="B3 (Brasil, Bolsa, Balcão)",
    about=("B3's historical quotation series: every paper traded on the exchange in each session since 1986, as "
           "fixed-width text files (245-character records: a header, one record a paper and market a session, a "
           "trailer) zipped one a year, a month or a day. Prices are in the currency and quotation of their time, not "
           "adjusted for inflation or for dividends, bonuses and subscription rights."),
    kind="feed",
    base="https://bvmf.bmfbovespa.com.br/InstDados/SerHist",
    version="the layout COTAHIST.AAAA.TXT, revision 02 (2020-10-05)",
    auth="none",
    limits=("none published (a public file server: a session's file about 600 KB, a year's 60 to 90 MB; asked at 10 "
            "a minute, 100 a run, 400 a day here). The Portuguese page forbids automated collection of what it offers "
            "(\"vedada a coleta automatizada\"), and B3's terms of use allow the site's data for personal use only, "
            "anything else by B3's prior written consent"),
    costs=("the files cost nothing; B3 also sends the series on CD-ROM by post for a fee (the whole base R$ 100, a "
           "year R$ 50, a month R$ 25, a day R$ 10)"),
    links=(
        Link("Historical quote data", QUOTES_EN, "docs"),
        Link("Cotações históricas (the page in Portuguese, with its notice on use)", QUOTES_PT, "docs"),
        Link("Séries históricas: the files to download", FILES_PAGE, "docs"),
        Link("Layout of the quotes file (revision 02, 2020-10-05)", LAYOUT, "spec"),
        Link("Historical quotations file layout (English, revision 01, 2017)", LAYOUT_EN, "spec"),
        Link("The series on CD-ROM: prices", PRICES, "pricing"),
        Link("Termos de uso", TERMS, "terms"),
        Link("Terms of use", TERMS_EN, "terms"),
    ),
    areas=(
        Area(YEARLY, "COTAHIST_A{YYYY}.ZIP: every session of a year, 1986 to the current one (the current year's "
                     "accumulates to its last session), 60 to 90 MB zipped", FILES_PAGE,
             covered="the history: the last B3_HISTORY_YEARS years (default 2), downloaded into sources/b3/ and "
                     "indexed (SQLite) beside each; fetched again when its last session is 20 weekdays behind"),
        Area(MONTHLY, "COTAHIST_M{MMYYYY}.ZIP: every session of a month, the last twelve months", FILES_PAGE),
        Area(DAILY, "COTAHIST_D{DDMMYYYY}.ZIP: one session, every paper's day, for the sessions of the current year; "
                    "out the evening of the session", FILES_PAGE,
             covered="the recent sessions, one request a session for every listing's day; the directory and records "
                     "from the last five sessions' files"),
        Area(FRAME, "type 00, the file's name, origin and date; type 99, the count of its records", LAYOUT,
             covered="checked: a file begins with its header, and the trailer's count matches the records read (a "
                     "file cut short is an error, never a short history)"),
        Area(CASH, "the spot trades of shares, units, funds, ETFs, BDRs, rights and receipts", LAYOUT,
             covered="kept: the standard lot (BDI 02, 05 to 09 and 11), real estate funds (12), agribusiness funds "
                     "(13) and ETFs (14), each price over its FATCOT (one share's); BDRs, rights, receipts, bonuses "
                     "and the rest left"),
        Area(ODD_LOT, "the fractional market's trades (codes ending in F)", LAYOUT),
        Area(OPTIONS, "call and put options, on stocks and indices, and their exercises", LAYOUT),
        Area(FORWARD, "the term market (termo), forwards with gain retention and with continuous movement", LAYOUT),
        Area(AUCTIONS, "papers traded at auction", LAYOUT),
        Area(TABLES, "the bulletin's classification of papers (BDI), their specification (ON, PN, UNT, CI ...), the "
                     "correction indicators of options and term contracts", LAYOUT,
             covered="read: the BDI decides what is kept, the ESPECI a listing's type (CS, ETF, FUND)"),
    ),
    calls=(
        Call("GET /COTAHIST_D{DDMMYYYY}.ZIP", "a session's file: every paper's day (a 404 for a day without a session, "
             "or one not out yet)", DAILY, used="the recent days of every listing, a request a session; the directory",
             docs=FILES_PAGE),
        Call("GET /COTAHIST_A{YYYY}.ZIP", "a year's file, every session of it", YEARLY,
             used="the history, kept in the data folder with its index (once a run at most)", docs=FILES_PAGE),
        Call("GET /COTAHIST_M{MMYYYY}.ZIP", "a month's file, every session of it (the last twelve months)", MONTHLY,
             docs=FILES_PAGE),
    ),
    verified=VERIFIED,
)

APIS = (COTAHIST,)
