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

# Load environment variables from .env in the project root
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(_ROOT, ".env"))
except ImportError:
    pass


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

def _call_gemini_api(model: str, system_prompt: str, user_prompt: str, temperature: float, json_mode: bool = False) -> str:
    """Robust caller for Gemini API using either google-genai, google-generativeai, or raw requests.

    When json_mode is True, the model is forced to emit a raw JSON object via responseMimeType,
    removing the need for fragile markdown-fence stripping (used by the Judge agent only).
    """
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
        generation_config = {"temperature": temperature}
        if json_mode:
            generation_config["responseMimeType"] = "application/json"
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": f"System Guidelines:\n{system_prompt}\n\nUser Context:\n{user_prompt}"}
                    ]
                }
            ],
            "generationConfig": generation_config
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
        genai_config = {"system_instruction": system_prompt, "temperature": temperature}
        if json_mode:
            genai_config["response_mime_type"] = "application/json"
        response = client.models.generate_content(
            model=model,
            contents=user_prompt,
            config=genai_config
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
        old_gen_config = {"temperature": temperature}
        if json_mode:
            old_gen_config["response_mime_type"] = "application/json"
        response = model_instance.generate_content(
            user_prompt,
            generation_config=old_gen_config
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
        kwargs = {
            "model": model,
            "max_tokens": 2048,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        try:
            resp = client.messages.create(**kwargs)
        except anthropic.BadRequestError as exc:
            # Newer models (e.g. Opus 4.8) deprecate the temperature knob — retry without it.
            if "temperature" in str(exc).lower() and "temperature" in kwargs:
                kwargs.pop("temperature", None)
                resp = client.messages.create(**kwargs)
            else:
                raise
        return resp.content[0].text or ""
    except Exception as exc:
        raise RuntimeError(f"Anthropic API call failed: {exc}")


def run_llm_call(provider: str, model: str, system_prompt: str, user_prompt: str, temperature: float, json_mode: bool = False) -> str:
    """Routes LLM execution to the configured provider.

    json_mode forces native JSON output where supported (Gemini). OpenAI/Anthropic already return
    clean text; the Judge step keeps a markdown-stripping fallback for those providers.
    """
    provider = provider.lower().strip()
    if provider == "gemini":
        return _call_gemini_api(model, system_prompt, user_prompt, temperature, json_mode=json_mode)
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
    bull_system = """You are a highly optimistic, momentum-focused technical analyst specializing in the Indian equity markets (NSE/BSE).
Your ONLY job is to construct the absolute strongest BUY argument for the provided stock.

You must focus heavily on:
1. Chart breakout patterns (e.g., Stage 2 continuations, VCP, flat bases, rounding bottoms), volume expansion parameters, and moving average alignments (e.g., 20 EMA, 50 DMA, 200 DMA structural support).
2. Relative Strength (RS) comparison against Nifty 50 and its sector benchmark.
3. Institutional tracking: signs of delivery volume spikes, steady accumulation blocks, or positive DII/FII buying interest.
4. High-velocity catalysts: massive new order inflows, defense/infra capex allocations, or positive fundamental trend changes in the news.

Keep your response sharp and concise (under 250 words). Focus strictly on data points supporting an aggressive swing long conviction."""

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
    bear_system = """You are a highly skeptical, risk-averse Red-Team auditor and forensic market short-seller in the Indian markets.
Your job is to read the provided Bullish Thesis and systematically dismantle its assumptions using the raw technical data and headlines.

You must identify and highlight:
1. Technical Traps: False breakouts on low volume, severe bearish RSI divergences on the daily frame, or overhead structural resistance columns.
2. Regulatory & Liquidity Risks: Proximity to upper/lower circuit limits, high promoter pledging percentages, or immediate risk of being moved into ASM (Additional Surveillance Measure) / GSM stages.
3. Structural F&O Friction: If the stock is highly vulnerable to repeatedly hitting the exchange F&O Ban list, draining institutional momentum.
4. Binary Risks: Crucial corporate actions or upcoming quarterly earnings results scheduled within the next 3 trading sessions.

Expose the hidden pitfalls that long-biased swing traders ignore due to confirmation bias. Keep your counter-argument under 250 words."""

    bear_user = f"""Construct the bearish counter-argument for {symbol} in the {sector} sector.

PROPOSED BULLISH THESIS TO DISMANTLE:
{bull_output}

RAW SYSTEM TECHNICAL STATS:
{tech_str}

RELEVANT HEADLINES & CONTEXT:
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
    judge_system = """You are the conservative, data-grounded Chief Investment Officer (CIO) for an elite Indian swing trading fund.
Your job is to cross-examine the Bullish Thesis and the Bearish Rebuttal against the raw data points and issue an absolute go/no-go trading directive using our standard 5-key framework.

Strict Risk Directives:
- Evaluate "TECHNICAL COMPLIANCE" internally: If the stock violates structural rules (e.g., trades below 50 DMA, has severe RSI divergence, or lacks institutional volume), you must force the first bullet point of your 'reasons' to start with "TECHNICAL COMPLIANCE: FAIL - [Reason]". Otherwise, start it with "TECHNICAL COMPLIANCE: PASS - [Reason]".
- If India VIX is above 18, or the general Trend Regime is Amber/Red, apply a strict 25% markdown to your conviction_score.
- Any major binary risk (earnings/board meet) within 3 days must automatically drop the verdict to "WATCH".
- The Risk-to-Reward (R:R) layout must mathematically project better than 1:2.

Your response must be in valid JSON format ONLY. Do not include markdown code wrappers (like ```json). Use our exact 5-key schema:
{
  "verdict": "BUY" | "WATCH" | "SKIP",
  "conviction_score": 1,
  "reasons": [
    "TECHNICAL COMPLIANCE: [PASS/FAIL] - [Brief technical justification]",
    "Momentum catalyst 1",
    "Momentum catalyst 2"
  ],
  "red_flags": [
    "Structural or macro risk 1",
    "Structural or macro risk 2"
  ],
  "rationale": "3-4 sentences outlining the executive summary of the debate, specific entry-range cautions, and how to handle position risk based on the findings."
}"""

    judge_user = f"""Deliver the final judgment for the stock: {symbol}.

BULL CASE THESIS:
{bull_output}

BEAR REBUTTAL:
{bear_output}

RAW DATA FEED FOR VERIFICATION:
Technical Stats: {tech_str}
Market Pulse: {market_str}
News Feeds: {news_str}"""

    logger.info("[debate] Running Judge Agent synthesis for %s using %s...", symbol, judge_cfg.get("model"))
    try:
        judge_raw = run_llm_call(
            provider=judge_cfg.get("provider", "gemini"),
            model=judge_cfg.get("model", "gemini-1.5-pro"),
            system_prompt=judge_system,
            user_prompt=judge_user,
            temperature=float(judge_cfg.get("temperature", 0.2)),
            json_mode=True
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

    # ── 6. Normalize keys & enforce deterministic risk markdown ────────────────
    # The Judge emits the 5-key schema (reasons / red_flags / rationale). Map these onto the
    # persisted output keys the dashboard already reads (top_triggers / top_red_flags /
    # judge_rationale), keeping old keys as a fallback for resilience.
    triggers = judge_json.get("reasons", judge_json.get("top_triggers", []))
    red_flags = judge_json.get("red_flags", judge_json.get("top_red_flags", []))
    rationale = judge_json.get("rationale", judge_json.get("judge_rationale", "No explanation provided."))

    # Code-enforced conviction markdown: LLMs are unreliable at arithmetic, so apply the
    # VIX>18 / Amber-Red 25% haircut deterministically using the real values in market_context.
    try:
        conviction_score = int(round(float(judge_json.get("conviction_score", 5))))
    except (TypeError, ValueError):
        conviction_score = 5
    nifty_ctx = market_context.get("nifty", {}) if isinstance(market_context, dict) else {}
    try:
        india_vix = float(nifty_ctx.get("vix", 0) or 0)
    except (TypeError, ValueError):
        india_vix = 0.0
    trend_regime = str(nifty_ctx.get("regime", "GREEN")).upper()
    if india_vix > 18 or trend_regime in ("AMBER", "RED"):
        marked_down = max(1, int(round(conviction_score * 0.75)))
        logger.info(
            "[debate] Applying 25%% conviction markdown for %s (VIX=%.2f, regime=%s): %d -> %d",
            symbol, india_vix, trend_regime, conviction_score, marked_down
        )
        conviction_score = marked_down

    # ── 7. Assemble & Save final Cache ─────────────────────────────────────────
    final_output = {
        "status": "success",
        "symbol": symbol,
        "date": date_str,
        "time": datetime.now().strftime("%H:%M"),
        "bull_case": bull_output,
        "bear_case": bear_output,
        "verdict": judge_json.get("verdict", "WATCH"),
        "conviction_score": conviction_score,
        "top_triggers": triggers,
        "top_red_flags": red_flags,
        "judge_rationale": rationale,
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
