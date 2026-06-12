import re
from decimal import Decimal

import httpx
from aws_lambda_powertools import Logger
from bs4 import BeautifulSoup

logger = Logger(child=True)

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


def _scrape_exposure(soup: BeautifulSoup, key: str) -> list[dict]:
    """
    Extract a name/percentage exposure list (e.g. countries or sectors) from a JustETF profile.

    `key` is the data-testid segment, such as "countries" or "sectors".
    """
    names = [
        el.get_text(strip=True)
        for el in soup.select(f'[data-testid="tl_etf-holdings_{key}_value_name"]')
    ]
    pcts = [
        _normalize_percentage(el.get_text(strip=True))
        for el in soup.select(f'[data-testid="tl_etf-holdings_{key}_value_percentage"]')
    ]
    return [
        {"name": name, "percentage": Decimal(pct)} for name, pct in zip(names, pcts, strict=True)
    ]


def scrape_fund(isin: str) -> dict:
    """
    Scrape top 10 holdings, country allocation, and sector allocation from JustETF for the
    given ISIN.

    Returns a dict compatible with FundSnapshot (without scrapedAt).
    """
    isin = validate_isin(isin)
    url = f"{BASE_URL}?isin={isin}"

    logger.info("Requesting JustETF profile", extra={"isin": isin, "url": url})
    try:
        response = httpx.get(
            url,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            timeout=30.0,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # A 404 from JustETF means the ISIN has no profile; surface it as such.
        logger.warning(
            "JustETF returned a non-2xx status",
            extra={"isin": isin, "status_code": exc.response.status_code},
        )
        if exc.response.status_code == 404:
            raise FundNotFoundError(f"No fund profile found for ISIN {isin}") from exc
        raise
    except httpx.RequestError as exc:
        # Network/timeout/DNS failure reaching JustETF — log so it is not an opaque 500.
        logger.exception(
            "Request to JustETF failed",
            extra={"isin": isin, "url": url, "error": str(exc)},
        )
        raise

    logger.info(
        "JustETF response received",
        extra={
            "isin": isin,
            "status_code": response.status_code,
            "content_length": len(response.text),
            "final_url": str(response.url),
        },
    )

    soup = BeautifulSoup(response.text, "html.parser")
    name_el = soup.find("h1")
    if not name_el:
        # Either a genuinely unknown ISIN, or JustETF served a non-profile page
        # (e.g. a cookie/consent wall or bot challenge) without the expected <h1>.
        logger.warning(
            "No <h1> found on JustETF page — treating as fund not found",
            extra={
                "isin": isin,
                "content_length": len(response.text),
                "final_url": str(response.url),
                "html_snippet": response.text[:500],
            },
        )
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
        logger.warning(
            "Profile found but no holdings data on page",
            extra={"isin": isin, "fund_name": name_el.get_text(strip=True)},
        )
        raise NoHoldingsDataError(f"No holdings data available for ISIN {isin}")

    top_holdings = [
        {"name": name, "percentage": Decimal(pct)}
        for name, pct in zip(holding_names, holding_pcts, strict=True)
    ]
    market_exposure = _scrape_exposure(soup, "countries")
    industry_exposure = _scrape_exposure(soup, "sectors")

    logger.info(
        "Parsed JustETF profile",
        extra={
            "isin": isin,
            "fund_name": name_el.get_text(strip=True),
            "holdings_count": len(top_holdings),
            "countries_count": len(market_exposure),
            "sectors_count": len(industry_exposure),
        },
    )

    return {
        "isin": isin,
        "name": name_el.get_text(strip=True),
        "topHoldings": top_holdings,
        "marketExposure": market_exposure,
        "industryExposure": industry_exposure,
        "source": "justetf",
    }
