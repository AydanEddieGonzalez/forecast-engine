# Makes real API calls with each test key and logs tokens + $ cost

import os
import time

import anthropic
import openai
from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from sqlalchemy import create_engine, text

load_dotenv()
DB_URL = os.environ["LOCAL_DB_URL"]
assert "localhost" in DB_URL or "127.0.0.1" in DB_URL, "Refusing: not the local DB"
engine = create_engine(DB_URL)

KEYS = {
    "01-anthropic": {
        "provider": "anthropic",
        "model": "claude-haiku-5-5",
        "price_model": "claude-haiku-5.5",
        "client": anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"]),
    },
    "01-openai": {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "price_model": "gpt-4o-mini",
        "client": openai.OpenAI(api_key=os.environ["CHATGPT_API_KEY"]),
    },
    "01-google": {
        "provider": "google",
        "model": "gemini-3.1-flash-lite",
        "price_model": "gemini-3.1-flash-lite",
        "client": genai.Client(api_key=os.environ["GOOGLE_API_KEY"]),
    },
}

# Still figuring out Azure
if os.getenv("AZURE_ENDPOINT") and os.getenv("AZURE_DEPLOYMENT"):
    KEYS["01-microsoft"] = {
        "provider": "azure",
        "price_provider": "openai", # WIP
        "model": os.environ["AZURE_DEPLOYMENT"],
        "price_model": "gpt-4o-mini", # WIP
        "client": openai.OpenAI(
            api_key=os.environ["MICROSOFT_API_KEY"],
            base_url=os.environ["AZURE_ENDPOINT"].rstrip("/") + "/openai/v1/",
        ),
    }
else:
    print("Skipping 01-microsoft: add AZURE_ENDPOINT and AZURE_DEPLOYMENT to .env")

MAX_TOKENS = 50
GOOGLE_MAX_TOKENS = 200 # Gemini's thinking tokens count toward this limit
CALLS_PER_KEY = 3


# return (input $ per 1M tokens, output $ per 1M tokens), latest scrape, standard tier
def get_prices(provider: str, model: str) -> tuple[float, float]:
    sql = text("""
        SELECT sku_type, price_per_1m_tokens_usd
        FROM staging.stg_pricing
        WHERE provider = :p AND model = :m AND is_active
          AND service_tier = 'standard'
          AND COALESCE(modality, '') <> 'audio'  -- Google lists audio input at a higher price
          AND sku_type IN ('input_tokens', 'output_tokens')
        ORDER BY ingested_at DESC
    """)

    with engine.connect() as conn:
        rows = conn.execute(sql, {"p": provider, "m": model}).fetchall()

    in_price = out_price = None
    for sku_type, price in rows:
        if sku_type == "input_tokens" and in_price is None:
            in_price = price
        elif sku_type == "output_tokens" and out_price is None:
            out_price = price

    if in_price is None or out_price is None:
        raise ValueError(f"No standard price for {provider}/{model}")
    return float(in_price), float(out_price)


# One call to return (input_tokens, output_tokens)
def call_model(key: dict, prompt: str) -> tuple[int, int]:
    client, model = key["client"], key["model"]
    messages = [{"role": "user", "content": prompt}]

    if key["provider"] == "anthropic":
        resp = client.messages.create(model=model, max_tokens=MAX_TOKENS, messages=messages)
        return resp.usage.input_tokens, resp.usage.output_tokens

    elif key["provider"] in ("openai", "azure"):
        resp = client.chat.completions.create(model=model, max_tokens=MAX_TOKENS, messages=messages)
        return resp.usage.prompt_tokens, resp.usage.completion_tokens

    elif key["provider"] == "google":
        resp = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(max_output_tokens=GOOGLE_MAX_TOKENS),
        )
        u = resp.usage_metadata

        # thinking tokens as output
        out = (u.candidates_token_count or 0) + (u.thoughts_token_count or 0)
        return u.prompt_token_count, out

    raise ValueError(f"Unknown provider {key['provider']}")

# look up prices before calling so money doesnt get wasted
def one_call(alias: str, prompt: str) -> dict:
    key = KEYS[alias]
    
    in_p, out_p = get_prices(key.get("price_provider", key["provider"]), key["price_model"])
    start = time.time()
    status, tin, tout = "ok", 0, 0

    try:
        tin, tout = call_model(key, prompt)
    except (anthropic.RateLimitError, openai.RateLimitError):
        status = "429"
    except genai_errors.ClientError as e:
        status = "429" if e.code == 429 else "error"
        print(alias, type(e).__name__, e.code)
    except Exception as e:
        status = "error"
        print(alias, type(e).__name__)

    cost = (tin * in_p + tout * out_p) / 1_000_000

    return {
        "key_alias": alias,
        "provider": key["provider"],
        "model": key["model"],
        "input_tokens": tin,
        "output_tokens": tout,
        "cost_usd": cost,
        "latency_ms": round((time.time() - start) * 1000),
        "status": status,
    }


def log_row(row: dict) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO token_router.key_usage
              (key_alias, provider, model, input_tokens, output_tokens, cost_usd, latency_ms, status)
            VALUES
              (:key_alias, :provider, :model, :input_tokens, :output_tokens, :cost_usd, :latency_ms, :status)
        """), row)


if __name__ == "__main__":
    for alias, key in KEYS.items():
        if "TODO" in (key["model"], key["price_model"]):
            print(f"Skipping {alias}: fill in model and price_model first")
            continue
        try:
            get_prices(key.get("price_provider", key["provider"]), key["price_model"])
        except ValueError as e:
            print(f"Skipping {alias}: {e}")
            continue

        for _ in range(CALLS_PER_KEY):
            row = one_call(alias, "Say hi in 5 words.")
            print(row)
            log_row(row)
