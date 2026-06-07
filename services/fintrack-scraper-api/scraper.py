import re
from decimal import Decimal

import httpx
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (compatible; Fintrack/1.0)"
BASE_URL = "https://www.justetf.com/uk/etf-profile.html"
ISIN_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


class ScraperError(Exception):
    pass


class FundNotFoundError(ScraperError):
    pass


class NoHoldingsDataError(ScraperError):
    pass


def validate_isin(isin: str) -> str:
    normalized = isin.strip().upper()
    if not ISIN_PATTERN.match(normalized):
        raise ValueError(f"Invalid ISIN format: {isin}")
    return normalized


def _normalize_percentage(value: str) -> str:
    return value.strip().removesuffix("%")


def scrape_fund(isin: str) -> dict:
    """
    Scrape top 10 holdings and country allocation from JustETF for the given ISIN.

    Returns a dict compatible with FundSnapshot (without scrapedAt).
    """
    isin = validate_isin(isin)
    url = f"{BASE_URL}?isin={isin}"

    response = httpx.get(
        url,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=30.0,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    name_el = soup.find("h1")
    if not name_el:
        raise FundNotFoundError(f"No fund profile found for ISIN {isin}")

    holding_names = [
        el.get_text(strip=True)
        for el in soup.select('[data-testid="tl_etf-holdings_top-holdings_link_name"]')
    ]
    holding_pcts = [
        _normalize_percentage(el.get_text(strip=True))
        for el in soup.select('[data-testid="tl_etf-holdings_top-holdings_value_percentage"]')
    ]

    if not holding_names:
        raise NoHoldingsDataError(f"No holdings data available for ISIN {isin}")

    country_names = [
        el.get_text(strip=True)
        for el in soup.select('[data-testid="tl_etf-holdings_countries_value_name"]')
    ]
    country_pcts = [
        _normalize_percentage(el.get_text(strip=True))
        for el in soup.select('[data-testid="tl_etf-holdings_countries_value_percentage"]')
    ]

    top_holdings = [
        {"name": name, "percentage": Decimal(pct)}
        for name, pct in zip(holding_names, holding_pcts, strict=True)
    ]
    market_exposure = [
        {"name": name, "percentage": Decimal(pct)}
        for name, pct in zip(country_names, country_pcts, strict=True)
    ]

    return {
        "isin": isin,
        "name": name_el.get_text(strip=True),
        "topHoldings": top_holdings,
        "marketExposure": market_exposure,
        "source": "justetf",
    }
