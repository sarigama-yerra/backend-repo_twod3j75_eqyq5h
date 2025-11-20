import os
from typing import List, Optional, Dict, Any

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

COINGECKO_API = "https://api.coingecko.com/api/v3"

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
    }

    # Check environment variables
    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"
    return response


# ---------- Helper functions ----------

def cg_get(path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    url = f"{COINGECKO_API}{path}"
    try:
        r = requests.get(url, params=params, timeout=15)
        if r.status_code == 429:
            raise HTTPException(status_code=429, detail="CoinGecko rate limit reached. Please try again shortly.")
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Upstream API error: {str(e)}")


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
        data = token_by_contract_ethereum(q)
        return {"type": "token", "data": data}

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
