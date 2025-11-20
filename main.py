import os
from typing import List, Optional, Dict, Any

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

COINGECKO_API = "https://api.coingecko.com/api/v3"
ETHERSCAN_API = "https://api.etherscan.io/api"
MESSARI_API = "https://data.messari.io/api/v2"

app = FastAPI(title="Crypto Intelligence API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskRequest(BaseModel):
    query: str


@app.get("/")
def read_root():
    return {"message": "Crypto Intelligence Backend Running"}


@app.get("/api/hello")
def hello():
    return {"message": "Hello from the backend API!"}


@app.get("/test")
def test_database():
    """Test endpoint to check if server is running and envs are visible"""
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Used (not required for this app)",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": [],
        "etherscan_api_key": "✅ Set" if os.getenv("ETHERSCAN_API_KEY") else "❌ Not Set",
        "messari_api_key": "✅ Set" if os.getenv("MESSARI_API_KEY") else "❌ Not Set",
    }

    # Check environment variables
    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"
    return response


# ---------- Helper functions ----------

def cg_get(path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    url = f"{COINGECKO_API}{path}"
    try:
        r = requests.get(url, params=params, timeout=20)
        if r.status_code == 429:
            raise HTTPException(status_code=429, detail="CoinGecko rate limit reached. Please try again shortly.")
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Upstream API error: {str(e)}")


def etherscan_total_supply(contract: str) -> Optional[str]:
    """Fetch total supply from Etherscan if API key is present (raw string, may include decimals)."""
    api_key = os.getenv("ETHERSCAN_API_KEY")
    if not api_key:
        return None
    try:
        params = {
            "module": "stats",
            "action": "tokensupply",
            "contractaddress": contract,
            "apikey": api_key,
        }
        r = requests.get(ETHERSCAN_API, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        if data.get("status") == "1":
            return data.get("result")
        return None
    except requests.RequestException:
        return None


def messari_profile(symbol: str) -> Optional[Dict[str, Any]]:
    """Attempt to fetch Messari profile for the given asset symbol (e.g., 'eth'). Requires API key for reliability."""
    headers = {}
    api_key = os.getenv("MESSARI_API_KEY")
    if api_key:
        headers["x-messari-api-key"] = api_key
    try:
        url = f"{MESSARI_API}/assets/{symbol.lower()}/profile"
        r = requests.get(url, headers=headers, timeout=20)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except requests.RequestException:
        return None


def first_item(value: Any) -> Optional[Any]:
    """Safely return the first item of a list-like value, else None."""
    if isinstance(value, list) and len(value) > 0:
        return value[0]
    return None


# ---------- Public Endpoints ----------

@app.get("/api/search")
def search_assets(q: str = Query(..., description="Coin or token search query (name or symbol)")):
    data = cg_get("/search", params={"query": q})
    # Return top matches for coins and tokens
    return {
        "coins": data.get("coins", [])[:10],
        "exchanges": data.get("exchanges", [])[:5],
        "icos": data.get("icos", [])[:5],
        "categories": data.get("categories", [])[:10],
    }


@app.get("/api/markets")
def markets(
    ids: Optional[str] = Query(None, description="Comma-separated coin IDs as per CoinGecko (e.g., bitcoin,ethereum)"),
    vs_currency: str = Query("usd", description="Quote currency"),
    per_page: int = Query(10, ge=1, le=250),
    page: int = Query(1, ge=1),
    sparkline: bool = Query(True),
):
    params = {
        "vs_currency": vs_currency,
        "order": "market_cap_desc",
        "per_page": per_page,
        "page": page,
        "sparkline": str(sparkline).lower(),
        "price_change_percentage": "1h,24h,7d",
    }
    if ids:
        params["ids"] = ids
    data = cg_get("/coins/markets", params=params)
    return data


@app.get("/api/coin/{coin_id}")
def coin_details(coin_id: str):
    data = cg_get(
        f"/coins/{coin_id}",
        params={
            "localization": "false",
            "tickers": "true",
            "market_data": "true",
            "community_data": "true",
            "developer_data": "true",
            "sparkline": "true",
        },
    )
    return data


@app.get("/api/token/ethereum/{address}")
def token_by_contract_ethereum(address: str):
    # CoinGecko supports Ethereum contract lookups under the ethereum platform
    data = cg_get(f"/coins/ethereum/contract/{address}")
    return data


@app.get("/api/token/ethereum/{address}/full")
def token_by_contract_ethereum_full(address: str):
    """Aggregate detailed token info from multiple sources. Returns a concise summary plus raw sources.
    - CoinGecko (core market + metadata)
    - Etherscan (total supply) if ETHERSCAN_API_KEY provided
    - Messari profile (founders/funding if available) if MESSARI_API_KEY provided
    """
    cg = cg_get(f"/coins/ethereum/contract/{address}")

    # Pull selected fields from CoinGecko
    market_data = cg.get("market_data", {}) or {}
    links = cg.get("links", {}) or {}
    image = (cg.get("image", {}) or {}).get("small")

    summary: Dict[str, Any] = {
        "name": cg.get("name"),
        "symbol": cg.get("symbol"),
        "image": image,
        "contract_address": address,
        "platform": "ethereum",
        "price": (market_data.get("current_price") or {}).get("usd"),
        "market_cap": (market_data.get("market_cap") or {}).get("usd"),
        "max_supply": market_data.get("max_supply"),
        "circulating_supply": market_data.get("circulating_supply"),
        "total_supply": market_data.get("total_supply"),
        "fully_diluted_valuation": (market_data.get("fully_diluted_valuation") or {}).get("usd"),
        "categories": cg.get("categories", []),
        "links": {
            "homepage": first_item(links.get("homepage")),
            "twitter": links.get("twitter_screen_name"),
            "discord": first_item(links.get("chat_url")),
            "github": first_item(((links.get("repos_url") or {}).get("github"))),
            "telegram": links.get("telegram_channel_identifier"),
        },
        "description": (cg.get("description", {}) or {}).get("en"),
        "community_data": cg.get("community_data"),
        "developer_data": cg.get("developer_data"),
    }

    # Etherscan total supply (raw)
    etherscan_supply_raw = etherscan_total_supply(address)

    # Messari profile (optional)
    messari = None
    symbol = cg.get("symbol") or ""
    if symbol:
        messari = messari_profile(symbol)

    return {
        "summary": summary,
        "sources": {
            "coingecko": cg,
            "etherscan": {"total_supply_raw": etherscan_supply_raw} if etherscan_supply_raw else None,
            "messari": messari,
        },
    }


@app.post("/api/ask")
def ask_bot(payload: AskRequest):
    """Simple intent router for voice/text queries.
    Examples:
    - "price of bitcoin" -> markets for bitcoin
    - "show ethereum chart" -> coin details with sparkline
    - "info 0x..." -> token by contract
    """
    q = payload.query.strip().lower()
    # Contract address intent (very naive eth address detection)
    if q.startswith("0x") and len(q) in (42, 66):
        data = token_by_contract_ethereum_full(q)
        return {"type": "token_full", "data": data}

    # Extract a potential coin name/symbol
    keywords = ["price of ", "price ", "chart of ", "chart ", "show ", "info "]
    name = q
    for kw in keywords:
        if kw in q:
            name = q.split(kw)[-1].strip()
            break

    # Try search -> markets
    s = search_assets(name)
    coins = s.get("coins", [])
    if not coins:
        raise HTTPException(status_code=404, detail="No matching assets found")
    top_ids = ",".join([c["id"] for c in coins[:5]])
    m = markets(ids=top_ids, vs_currency="usd", per_page=5, page=1, sparkline=True)
    return {"type": "markets", "query": name, "data": m}


# --------------- Run ---------------
if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
