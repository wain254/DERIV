import os
import secrets
import json
import urllib.parse
import urllib.request
import urllib.error
import asyncio
from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from dotenv import load_dotenv
import logging
from deriv_client import DerivClient

load_dotenv()

APP_ID = os.getenv("DERIV_APP_ID", "1089")
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:8000/oauth/callback")
SKIP_OAUTH = os.getenv("SKIP_OAUTH", "false").lower() == "true"
MOCK_SCAN = os.getenv("MOCK_SCAN", "false").lower() == "true"   # <-- new

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

sessions = {}

def get_current_user(request: Request):
    session_token = request.cookies.get("session_id")
    if session_token and session_token in sessions:
        return sessions[session_token]
    return None

# ---------- PUBLIC LANDING PAGE ----------
@app.get("/")
async def landing():
    return FileResponse("static/landing.html")

# ---------- PROTECTED DASHBOARD ----------
@app.get("/dashboard")
async def dashboard(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/")
    return FileResponse("static/dashboard.html")

# ---------- DERIV OAUTH LOGIN (with SKIP_OAUTH bypass) ----------
@app.get("/auth/login")
async def login_with_deriv():
    if SKIP_OAUTH:
        token = os.getenv("DERIV_API_TOKEN")
        if not token:
            raise HTTPException(status_code=400, detail="DERIV_API_TOKEN missing in .env")
        session_id = secrets.token_urlsafe(32)
        sessions[session_id] = {
            "access_token": token,
            "user": "DEV_USER"
        }
        response = RedirectResponse("/dashboard")
        response.set_cookie(key="session_id", value=session_id, httponly=True)
        return response

    state = secrets.token_urlsafe(16)
    sessions[state] = {"status": "pending"}
    auth_url = (
        f"https://oauth.deriv.com/oauth2/authorize"
        f"?app_id={APP_ID}"
        f"&l=EN"
        f"&brand=deriv"
        f"&redirect_uri={REDIRECT_URI}"
        f"&state={state}"
    )
    return RedirectResponse(auth_url)

# ---------- OAUTH CALLBACK ----------
@app.get("/oauth/callback")
async def oauth_callback(request: Request):
    params = dict(request.query_params)
    code = params.get("code")
    state = params.get("state")

    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code.")
    if not state or state not in sessions:
        raise HTTPException(status_code=400, detail="Invalid state parameter.")

    token_url = "https://oauth.deriv.com/oauth2/token"
    token_body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "app_id": APP_ID,
    }).encode("utf-8")
    token_request = urllib.request.Request(
        token_url,
        data=token_body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    try:
        with urllib.request.urlopen(token_request) as token_response:
            response_body = token_response.read().decode("utf-8")
            token_data = json.loads(response_body)
            if token_response.getcode() != 200:
                logger.error(f"Token exchange failed: {token_data}")
                raise HTTPException(status_code=400, detail="Failed to obtain access token.")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8") if hasattr(exc, "read") else ""
        logger.error(f"Token exchange failed: {error_body}")
        raise HTTPException(status_code=400, detail="Failed to obtain access token.")
    except urllib.error.URLError as exc:
        logger.error(f"Token exchange failed: {exc.reason}")
        raise HTTPException(status_code=400, detail="Failed to obtain access token.")

    access_token = token_data.get("access_token")

    session_id = secrets.token_urlsafe(32)
    sessions[session_id] = {
        "access_token": access_token,
        "user": "DOT92974900"
    }

    response = RedirectResponse("/dashboard")
    response.set_cookie(key="session_id", value=session_id, httponly=True)
    return response

# -----------------------------------------------------------------
# 🚀 AI MARKET SCANNER (with MOCK fallback)
# -----------------------------------------------------------------
@app.get("/api/scan")
async def scan_markets(request: Request):
    # If mock mode is enabled, return simulated data
    if MOCK_SCAN:
        mock_markets = [
            {"name": "Volatility 100 (1s) Index", "type": "Even/Odd", "score": 94.2, "win_rate": 68.5, "bias": "Even 8.5%", "momentum": 1.2},
            {"name": "Volatility 75 Index", "type": "Rise/Fall", "score": 87.0, "win_rate": 62.3, "bias": "Odd 6.2%", "momentum": -0.8},
            {"name": "Volatility 50 Index", "type": "Even/Odd", "score": 78.5, "win_rate": 58.9, "bias": "Even 4.1%", "momentum": 0.5},
            {"name": "Step Index", "type": "Rise/Fall", "score": 91.3, "win_rate": 66.0, "bias": "Even 7.3%", "momentum": 2.1},
            {"name": "Boom 500 Index", "type": "Match/Differ", "score": 82.1, "win_rate": 60.1, "bias": "Odd 3.9%", "momentum": -1.3},
        ]
        mock_markets.sort(key=lambda x: x["score"], reverse=True)
        return {
            "best": mock_markets[0],
            "all_markets": mock_markets,
            "total_scanned": len(mock_markets),
            "timestamp": "2026-07-28 14:20:00 GMT",
            "mock": True  # frontend can display "Mock Mode" if needed
        }

    # Otherwise, attempt real scan
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = user.get("access_token")
    if not token:
        raise HTTPException(status_code=400, detail="No access token found")

    symbols = ["R_100", "R_75", "1HZ1000V", "R_50", "R_25"]

    async def scan_symbol(symbol):
        client = DerivClient(app_id=APP_ID, token=token, mode="sandbox")
        ticks_data = []
        try:
            await client.connect()
            def collect_tick(data):
                if "tick" in data and data["tick"].get("symbol") == symbol:
                    ticks_data.append(data["tick"])
            client.on("tick", collect_tick)
            await client.send({"subscribe": "ticks", "symbol": symbol})
            waited = 0
            while len(ticks_data) < 10 and waited < 50:
                await asyncio.sleep(0.1)
                waited += 1
            await client.send({"forget_all": "ticks"})
            await client.close()
        except Exception as e:
            logger.error(f"Error scanning {symbol}: {e}")
            return None

        if len(ticks_data) < 5:
            return None

        evens = sum(1 for t in ticks_data if float(str(t["quote"]).split(".")[-1][-1]) % 2 == 0)
        total = len(ticks_data)
        even_pct = (evens / total) * 100
        odd_pct = 100 - even_pct
        score = abs(even_pct - 50) * 2

        first = float(ticks_data[0]["quote"])
        last = float(ticks_data[-1]["quote"])
        momentum = ((last - first) / first) * 100

        return {
            "name": symbol,
            "type": "Even/Odd",
            "score": round(score, 1),
            "win_rate": round(max(even_pct, odd_pct), 1),
            "bias": f"{'Even' if even_pct > odd_pct else 'Odd'} {round(abs(even_pct - odd_pct), 1)}%",
            "momentum": round(momentum, 2)
        }

    tasks = [scan_symbol(sym) for sym in symbols]
    scan_results = await asyncio.gather(*tasks)
    valid_results = [r for r in scan_results if r is not None]

    if not valid_results:
        return {"error": "Could not fetch data for any symbol. Check your token or network."}

    valid_results.sort(key=lambda x: x["score"], reverse=True)
    return {
        "best": valid_results[0],
        "all_markets": valid_results,
        "total_scanned": len(valid_results),
        "timestamp": "2026-07-28 14:20:00 GMT",
        "mock": False
    }

# ---------- HEALTH ----------
@app.get("/health")
async def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)