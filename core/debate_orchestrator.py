import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Resolve path to project root
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEBATE_CONFIG_PATH = os.path.join(_ROOT, "data", "debate_config.json")
CACHE_DIR = os.path.join(_ROOT, "data", "due_diligence")

# ── Configuration Loader ───────────────────────────────────────────────────────

def load_debate_config() -> Dict[str, Any]:
    """Loads the debate configuration, creating defaults if missing."""
    default_config = {
        "bull_agent": {
            "provider": "gemini",
            "model": "gemini-1.5-flash",
            "temperature": 0.4
        },
        "bear_agent": {
            "provider": "gemini",
            "model": "gemini-1.5-flash",
            "temperature": 0.4
        },
        "judge_agent": {
            "provider": "gemini",
            "model": "gemini-1.5-pro",
            "temperature": 0.2
        }
    }
    if not os.path.exists(DEBATE_CONFIG_PATH):
        os.makedirs(os.path.dirname(DEBATE_CONFIG_PATH), exist_ok=True)
        try:
            with open(DEBATE_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(default_config, f, indent=2)
        except Exception as exc:
            logger.warning("[debate] Failed to write default config: %s", exc)
        return default_config
    try:
        with open(DEBATE_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("[debate] Config load failed (%s) — using defaults", exc)
        return default_config


def save_debate_config(config: Dict[str, Any]) -> None:
    """Saves the debate configuration to disk."""
    os.makedirs(os.path.dirname(DEBATE_CONFIG_PATH), exist_ok=True)
    with open(DEBATE_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

# ── Unified API Ingestion / Callers ───────────────────────────────────────────

def _call_gemini_api(model: str, system_prompt: str, user_prompt: str, temperature: float) -> str:
    """Robust caller for Gemini API using either google-genai, google-generativeai, or raw requests."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise ValueError(
            "Missing GEMINI_API_KEY in your .env file. "
            "Please add it to enable the debate chamber with Gemini models."
        )

    # Fallback 1: Raw requests POST (highly resilient, zero-dependency, extremely fast)
    try:
        import requests
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": f"System Guidelines:\n{system_prompt}\n\nUser Context:\n{user_prompt}"}
                    ]
                }
            ],
            "generationConfig": {
                "temperature": temperature
            }
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        
        # Parse output text
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            if parts:
                return parts[0].get("text", "")
        raise ValueError(f"Gemini API returned unexpected structure: {data}")
    except Exception as exc:
        logger.warning("[debate] Gemini HTTP fallback failed: %s. Trying client libraries...", exc)
        # If it is a real Gemini API Client Error (e.g. 429 Quota Exceeded, 400 Bad Request, 403 Forbidden, 404 Not Found),
        # immediately raise it! Continuing to other fallbacks only masks the true API quota or credential problem.
        if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
            try:
                err_json = exc.response.json()
                err_msg = err_json.get("error", {}).get("message", str(exc))
                raise ValueError(f"Gemini API Error ({exc.response.status_code}): {err_msg}")
            except Exception:
                raise ValueError(f"Gemini API HTTP Error ({exc.response.status_code}): {exc}")

    # Fallback 2: google-genai new SDK
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=user_prompt,
            config={"system_instruction": system_prompt, "temperature": temperature}
        )
        if response.text:
            return response.text
    except Exception as e_new:
        logger.warning("[debate] New google-genai library call failed: %s", e_new)

    # Fallback 3: google-generativeai old SDK
    try:
        import google.generativeai as google_genai
        google_genai.configure(api_key=api_key)
        model_instance = google_genai.GenerativeModel(
            model_name=model,
            system_instruction=system_prompt
        )
        response = model_instance.generate_content(
            user_prompt,
            generation_config={"temperature": temperature}
        )
        if response.text:
            return response.text
    except Exception as e_old:
        raise RuntimeError(f"All Gemini API connection methods failed. Details: {e_old}")


def _call_openai_api(model: str, system_prompt: str, user_prompt: str, temperature: float) -> str:
    """Robust caller for OpenAI compatible models."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError(
            "Missing OPENAI_API_KEY in your .env file. "
            "Please add it to enable the debate chamber with OpenAI models."
        )
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=temperature
        )
        return resp.choices[0].message.content or ""
    except Exception as exc:
        raise RuntimeError(f"OpenAI API call failed: {exc}")


def _call_anthropic_api(model: str, system_prompt: str, user_prompt: str, temperature: float) -> str:
    """Robust caller for Anthropic models."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise ValueError(
            "Missing ANTHROPIC_API_KEY in your .env file. "
            "Please add it to enable the debate chamber with Anthropic models."
        )
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model,
            max_tokens=2048,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        return resp.content[0].text or ""
    except Exception as exc:
        raise RuntimeError(f"Anthropic API call failed: {exc}")


def run_llm_call(provider: str, model: str, system_prompt: str, user_prompt: str, temperature: float) -> str:
    """Routes LLM execution to the configured provider."""
    provider = provider.lower().strip()
    if provider == "gemini":
        return _call_gemini_api(model, system_prompt, user_prompt, temperature)
    elif provider == "openai":
        return _call_openai_api(model, system_prompt, user_prompt, temperature)
    elif provider == "anthropic":
        return _call_anthropic_api(model, system_prompt, user_prompt, temperature)
    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")

# ── Debate Engine Logic ────────────────────────────────────────────────────────

def run_adversarial_debate(
    symbol: str,
    technicals: Dict[str, Any],
    recent_news: List[Dict[str, Any]],
    market_context: Dict[str, Any],
    sector: str,
    override_config: Optional[Dict[str, Any]] = None,
    force_refresh: bool = False,
    check_only: bool = False
) -> Dict[str, Any]:
    """
    Executes a structured adversarial debate (Bull vs. Bear) on a stock setup
    and synthesizes a logical final judgment with caching.
    """
    symbol = symbol.strip().upper()
    date_str = datetime.now().strftime("%Y-%m-%d")
    
    # ── 1. Cache Check ─────────────────────────────────────────────────────────
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, f"{symbol}_{date_str}.json")
    
    if not force_refresh and os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
                cached_data["cached"] = True
                logger.info("[debate] Serving cached debate for %s", symbol)
                return cached_data
        except Exception as exc:
            logger.warning("[debate] Failed to read cached file (%s), re-running...", exc)

    if check_only:
        return {"status": "no_cache", "cached": False}

    # ── 2. Ingest Config & Inputs ──────────────────────────────────────────────
    cfg = load_debate_config()
    if override_config:
        cfg.update(override_config)

    bull_cfg = cfg.get("bull_agent", {"provider": "gemini", "model": "gemini-1.5-flash", "temperature": 0.4})
    bear_cfg = cfg.get("bear_agent", {"provider": "gemini", "model": "gemini-1.5-flash", "temperature": 0.4})
    judge_cfg = cfg.get("judge_agent", {"provider": "gemini", "model": "gemini-1.5-pro", "temperature": 0.2})

    # Prepare detailed context variables
    tech_str = json.dumps(technicals, indent=2)
    news_titles = [f"- {n.get('title', '')} (Sentiment: {n.get('sentiment', {}).get('label', 'neutral')})" for n in recent_news]
    news_str = "\n".join(news_titles) if news_titles else "No recent headlines available."
    market_str = json.dumps(market_context, indent=2)

    # ── 3. Step 1: Bull Agent Call ─────────────────────────────────────────────
    bull_system = """You are a highly optimistic, momentum-focused technical and fundamental analyst. 
Your ONLY job is to construct the absolute strongest BUY argument for the provided stock.
You must focus heavily on:
1. Chart breakout patterns, consolidation setups, volume spikes, and rising averages.
2. Relative strength compared to the sector and macro market.
3. Positive corporate actions, earnings catalysts, new order contracts, or structural growth stories in the news.
4. Explaining why recent volatility represents a healthy pullback or accumulation rather than structural weakness.

Keep your response extremely professional, sharp, and concise (under 250 words). Focus only on factors supporting a BUY conviction."""

    bull_user = f"""Construct the BUY case for the stock: {symbol} in the {sector} sector.

TECHNICAL STATS:
{tech_str}

RELEVANT HEADLINES & SENTIMENT:
{news_str}

MACRO MARKET CONTEXT:
{market_str}"""

    logger.info("[debate] Running Bull Agent analysis for %s using %s...", symbol, bull_cfg.get("model"))
    try:
        bull_output = run_llm_call(
            provider=bull_cfg.get("provider", "gemini"),
            model=bull_cfg.get("model", "gemini-1.5-flash"),
            system_prompt=bull_system,
            user_prompt=bull_user,
            temperature=float(bull_cfg.get("temperature", 0.4))
        ).strip()
    except Exception as exc:
        logger.error("[debate] Bull Agent execution failed: %s", exc)
        return {"status": "error", "message": f"Bull Agent failed: {exc}"}

    # ── 4. Step 2: Bear Agent Call ─────────────────────────────────────────────
    bear_system = """You are a highly skeptical, risk-averse Red-Team short seller and risk auditor.
Your ONLY job is to construct the absolute strongest argument for NOT buying this stock (SKIP / WATCH).
You must search for and highlight:
1. Hidden chart weaknesses: overhead resistance levels, declining support channels, or negative RSI divergences.
2. Macro and Sector risks: sector overextension, global index volatility, or general market relative weakness.
3. Negative fundamental disclosures in corporate releases: promoter pledging increases, auditor concerns, tax disputes, high debt, or promoter selling.
4. Key volatility risks: upcoming high-risk binary events like board meetings or earnings announcements in the next few days.

Do not pull any punches. Your value lies in exposing hidden pitfalls that bullish traders miss due to confirmation bias. Keep your argument under 250 words."""

    bear_user = f"""Construct the bearish Red-Team "DON'T BUY" case for the stock: {symbol} in the {sector} sector.

TECHNICAL STATS:
{tech_str}

RELEVANT HEADLINES & SENTIMENT:
{news_str}

MACRO MARKET CONTEXT:
{market_str}"""

    logger.info("[debate] Running Bear Agent analysis for %s using %s...", symbol, bear_cfg.get("model"))
    try:
        bear_output = run_llm_call(
            provider=bear_cfg.get("provider", "gemini"),
            model=bear_cfg.get("model", "gemini-1.5-flash"),
            system_prompt=bear_system,
            user_prompt=bear_user,
            temperature=float(bear_cfg.get("temperature", 0.4))
        ).strip()
    except Exception as exc:
        logger.error("[debate] Bear Agent execution failed: %s", exc)
        return {"status": "error", "message": f"Bear Agent failed: {exc}"}

    # ── 5. Step 3: The Judge Call ──────────────────────────────────────────────
    judge_system = """You are a highly conservative, logical Chief Investment Officer (CIO) and Portfolio Manager.
Your job is to weigh the Bullish Thesis and Bearish Thesis presented by your analyst agents and issue a final, definitive decision.

Strict Risk Policy constraints:
- Risk-to-Reward (R:R) must be favorable (ideally > 1:2 entry zone).
- High binary risks (e.g. quarterly earnings releases in less than 3 days) must be treated with absolute caution (recommending WATCH rather than BUY).
- Technical gatekeeper conditions must be respected.

Your response must be in valid JSON format ONLY. Do not include markdown code wrappers (like ```json) or leading/trailing text. Output a JSON object with the exact keys:
{
  "verdict": "BUY" | "WATCH" | "SKIP",
  "conviction_score": 1-10,
  "top_triggers": ["List the top 2 bullish triggers supporting the setup"],
  "top_red_flags": ["List the top 2 bearish flags or warning points"],
  "judge_rationale": "Detail your objective final verdict and execution plan in 3-4 sentences"
}"""

    judge_user = f"""Deliver the final judgment for the stock: {symbol}.

BULL CASE ARGUMENT (Optimist Analyst):
{bull_output}

BEAR CASE ARGUMENT (Red-Team Auditor):
{bear_output}

ORIGINAL TECHNICAL DATA:
{tech_str}"""

    logger.info("[debate] Running Judge Agent synthesis for %s using %s...", symbol, judge_cfg.get("model"))
    try:
        judge_raw = run_llm_call(
            provider=judge_cfg.get("provider", "gemini"),
            model=judge_cfg.get("model", "gemini-1.5-pro"),
            system_prompt=judge_system,
            user_prompt=judge_user,
            temperature=float(judge_cfg.get("temperature", 0.2))
        ).strip()
        
        # Clean potential markdown block wrappers if model ignores instructions
        if judge_raw.startswith("```"):
            lines = judge_raw.splitlines()
            if lines[0].startswith("```json") or lines[0].startswith("```"):
                judge_raw = "\n".join(lines[1:-1])
        
        judge_json = json.loads(judge_raw)
    except Exception as exc:
        logger.error("[debate] Judge Agent execution or JSON parsing failed: %s. Raw output: %s", exc, judge_raw if 'judge_raw' in locals() else '')
        return {
            "status": "error",
            "message": f"Judge failed to generate valid structured data: {exc}"
        }

    # ── 6. Assemble & Save final Cache ─────────────────────────────────────────
    final_output = {
        "status": "success",
        "symbol": symbol,
        "date": date_str,
        "time": datetime.now().strftime("%H:%M"),
        "bull_case": bull_output,
        "bear_case": bear_output,
        "verdict": judge_json.get("verdict", "WATCH"),
        "conviction_score": judge_json.get("conviction_score", 5),
        "top_triggers": judge_json.get("top_triggers", []),
        "top_red_flags": judge_json.get("top_red_flags", []),
        "judge_rationale": judge_json.get("judge_rationale", "No explanation provided."),
        "cached": False,
        "models_used": {
            "bull_agent": bull_cfg.get("model", "unknown"),
            "bear_agent": bear_cfg.get("model", "unknown"),
            "judge_agent": judge_cfg.get("model", "unknown")
        }
    }

    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(final_output, f, indent=2)
        logger.info("[debate] Saved debate cache for %s", symbol)
    except Exception as exc:
        logger.warning("[debate] Failed to save cache JSON for %s: %s", symbol, exc)

    return final_output

# Standalone smoke test
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    print("Testing Debate Engine stand-alone...")
    # Add dummy inputs
    sample_tech = {"price": 750, "change_pct": 1.5, "rsi": 62, "ema20": 725, "volume_ratio": 2.1}
    sample_news = [{"title": "SBI reports massive surge in retail credit demand", "sentiment": {"label": "positive"}}]
    sample_mkt = {"nifty": {"level": 22410, "change_pct": 0.3}, "sentiment": "Bullish", "fii_dii": {"fii_today": 1200}}
    
    # Run standalone with Gemini Flash/Pro if keys are in environment
    try:
        res = run_adversarial_debate("SBIN", sample_tech, sample_news, sample_mkt, "BANK", force_refresh=True)
        print(json.dumps(res, indent=2))
    except Exception as e:
        print(f"Standalone scan skipped/failed: {e}")
