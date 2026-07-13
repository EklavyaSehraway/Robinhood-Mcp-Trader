"""AI second-opinion layer (Claude Opus 4.8 on Amazon Bedrock).

Runs AFTER the quantitative scan, not instead of it: the scanner finds
candidates with technicals; Claude reviews each with its recent news and
returns a verdict. It can veto a pick (earnings tomorrow, pending lawsuit,
sector-wide bad news the price hasn't digested) or endorse it — context a
price-only scanner is blind to.

Design constraints (keep it non-intrusive):
  - One API call per scan cycle (all candidates batched), ~every 30 min.
  - The AI can only VETO or ENDORSE from the scanner's list — it never
    introduces its own tickers, so risk limits stay intact.
  - On any AI/API failure the scan proceeds unreviewed (fail-open, flagged).
"""
from __future__ import annotations

import json
import logging

from anthropic import AnthropicBedrockMantle, APIError, APIConnectionError

import config
from news import fetch_news

log = logging.getLogger("ai_analyst")

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "market_note": {
            "type": "string",
            "description": "1-2 sentence read on overall conditions for a 1-week long swing",
        },
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["endorse", "neutral", "veto"]},
                    "confidence": {"type": "integer", "description": "1-10"},
                    "reason": {"type": "string", "description": "One sentence, cite specifics"},
                },
                "required": ["symbol", "verdict", "confidence", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["market_note", "verdicts"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are a risk-focused equity analyst reviewing candidates \
from a quantitative weekly-swing scanner (max 5-trading-day holds, long only). \
The technicals already passed; your job is catching what price data misses: \
imminent earnings dates, pending litigation/regulatory action, guidance cuts, \
sector contagion, or news the price hasn't digested. Veto only with a concrete \
reason — disagreeing with the technical setup is not grounds. Endorse when news \
flow supports the setup. Otherwise stay neutral. Be decisive and specific."""


def _build_prompt(recs: list[dict], news: dict[str, list[dict]], spy_1m: float) -> str:
    lines = [
        f"Market context: SPY 1-month return {spy_1m:+.1f}%.",
        "",
        "Candidates (symbol | setup | price | stop | target | 12w momentum | rationale):",
    ]
    for r in recs:
        lines.append(
            f"\n## {r['symbol']} — {r['setup']} @ ${r['price']} "
            f"(stop ${r['stop']}, target ${r['target']}, 12w {r['mom_12w']:+.1f}%)"
        )
        lines.append(f"Scanner rationale: {r['rationale']}")
        items = news.get(r["symbol"], [])
        if items:
            lines.append("Recent headlines:")
            for h in items:
                age = f"{h['age_hours']}h ago" if h["age_hours"] is not None else "recent"
                lines.append(f"- [{age}] {h['title']} ({h['publisher']})")
                if h["summary"]:
                    lines.append(f"  {h['summary']}")
        else:
            lines.append("Recent headlines: none found in the last 48h.")
    lines.append(
        "\nReturn a verdict for every candidate listed above. "
        "Today's date context: this is a live scan during US market hours."
    )
    return "\n".join(lines)


def review_candidates(recs: list[dict], spy_1m: float) -> dict:
    """Returns {"reviewed": bool, "market_note": str, "verdicts": {sym: {...}}}."""
    if not recs:
        return {"reviewed": False, "market_note": "", "verdicts": {}}

    symbols = [r["symbol"] for r in recs]
    log.info("fetching news for %d candidates: %s", len(symbols), ", ".join(symbols))
    news = fetch_news(symbols)
    total_headlines = sum(len(v) for v in news.values())
    log.info("fetched %d headlines across %d symbols", total_headlines, len(news))

    client = AnthropicBedrockMantle(
        aws_access_key=config.AWS_ACCESS_KEY_ID,
        aws_secret_key=config.AWS_SECRET_ACCESS_KEY,
        aws_region=config.AWS_REGION,
    )
    log.info("sending %d candidates to Claude Opus 4.8 for review...", len(recs))
    # Bedrock Mantle rejects output_config.format, so JSON shape is enforced
    # by prompt + schema-in-prompt and parsed tolerantly below.
    json_instruction = (
        "\n\nRespond with ONLY a JSON object (no markdown fences, no prose) "
        "matching this schema:\n" + json.dumps(VERDICT_SCHEMA)
    )
    try:
        response = client.messages.create(
            model=config.BEDROCK_MODEL,
            max_tokens=4096,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": _build_prompt(recs, news, spy_1m) + json_instruction,
            }],
        )
        if response.stop_reason == "refusal":
            log.warning("AI review refused; proceeding unreviewed")
            return {"reviewed": False, "market_note": "", "verdicts": {}}
        text = next(b.text for b in response.content if b.type == "text")
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise json.JSONDecodeError("no JSON object in response", text, 0)
        data = json.loads(text[start : end + 1])
    except (APIError, APIConnectionError, StopIteration, json.JSONDecodeError) as e:
        log.warning("AI review failed (%s); proceeding unreviewed", e)
        return {"reviewed": False, "market_note": "", "verdicts": {}}

    verdicts = {
        v["symbol"]: {
            "verdict": v["verdict"],
            "confidence": v["confidence"],
            "reason": v["reason"],
        }
        for v in data.get("verdicts", [])
        if v.get("symbol") in set(symbols)  # ignore any hallucinated tickers
    }
    endorse = [s for s, v in verdicts.items() if v["verdict"] == "endorse"]
    veto = [s for s, v in verdicts.items() if v["verdict"] == "veto"]
    log.info("AI review done: %d endorse (%s), %d veto (%s), %d neutral",
             len(endorse), ",".join(endorse) or "none",
             len(veto), ",".join(veto) or "none",
             len(verdicts) - len(endorse) - len(veto))
    return {
        "reviewed": True,
        "market_note": data.get("market_note", ""),
        "verdicts": verdicts,
        "news": news,
    }


def apply_verdicts(recs: list[dict], review: dict) -> list[dict]:
    """Annotate recommendations; drop vetoed ones from the tradeable list.

    Endorsed picks get a small score boost so they rank first; vetoed picks
    stay visible on the dashboard (flagged) but are excluded from trading.
    """
    if not review.get("reviewed"):
        for r in recs:
            r["ai"] = None
        return recs

    verdicts = review["verdicts"]
    for r in recs:
        v = verdicts.get(r["symbol"])
        r["ai"] = v
        if v and v["verdict"] == "endorse":
            r["score"] = round(r["score"] + 5, 1)
    recs.sort(key=lambda r: r["score"], reverse=True)
    return recs
