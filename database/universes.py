# database/universes.py
"""
Named symbol universes.

Kept apart from database/ingest.py so that resolving a universe name for a
backtest (core/batch/batch_config.py) does not depend on the CSV loader.

TECH_100 is also the default ingest set (scripts/build_database.py without --all).
"""

TECH_100 = {
    # Giants (Mag 7 + Big Tech)
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "NVDA", "META", "TSLA",
    # Semiconductors
    "AVGO", "AMD", "QCOM", "TXN", "MU", "AMAT", "LRCX", "ADI", "KLAC",
    "MRVL", "NXPI", "MCHP", "ON", "GFS", "ARM", "INTC", "TSM", "ASML",
    "STM", "TER", "ENTG", "SWKS", "QRVO", "WOLF", "LSCC",
    # Software / SaaS / Cloud
    "CRM", "ADBE", "ORCL", "INTU", "NOW", "IBM", "WDAY", "SNPS", "CDNS",
    "ADSK", "PANW", "FTNT", "CRWD", "PLTR", "DDOG", "ZS", "ANET", "TEAM",
    "HUBS", "NET", "DOCU", "OKTA", "MDB", "DT", "ZM", "SSNC", "TYL",
    "PTC", "GEN", "CHKP", "AKAM", "CFLT", "GTLB", "PATH", "SNOW", "TWLO",
    "PCTY", "PAYC", "MANH", "OTEX",
    # Internet / E-commerce / Apps
    "NFLX", "BKNG", "ABNB", "UBER", "DASH", "SHOP", "MELI", "PDD", "JD",
    "BABA", "EBAY", "ETSY", "LYFT", "SNAP", "PINS", "RBLX", "DKNG", "HOOD",
    "COIN", "TTD", "APP", "DUOL", "Z",
    # Hardware / Networking
    "CSCO", "DELL", "HPQ", "HPE", "GLW", "STX", "WDC", "NTAP", "SMCI",
    "PSTG", "IONQ",
    # Fintech / Payments
    # Fiserv trades as FI through 2025-10-31 and as FISV from 2025-11-11, with
    # six sessions where neither ticker has data. FI is listed because it covers
    # 478 sessions against FISV's 13, but neither alone spans the window -- see
    # ADR-024. A backtest ending after 2025-10-31 will report reduced session
    # coverage for this symbol, which is correct rather than a bug.
    "V", "MA", "PYPL", "FIS", "FI", "GPN", "AFRM", "TOST"
}
