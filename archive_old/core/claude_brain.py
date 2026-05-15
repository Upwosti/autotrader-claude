"""
Claude Brain — calls Anthropic API for trade analysis and evolution reasoning.
"""

import os
from loguru import logger
from config import ANTHROPIC_API_KEY

try:
    import anthropic
    _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    CLAUDE_AVAILABLE = bool(ANTHROPIC_API_KEY)
except Exception:
    _client = None
    CLAUDE_AVAILABLE = False


MODEL = "claude-opus-4-5"
MAX_TOKENS = 512


def _ask(prompt: str, system: str = "") -> str:
    if not CLAUDE_AVAILABLE or _client is None:
        return "[Claude unavailable]"
    try:
        msgs = [{"role": "user", "content": prompt}]
        kwargs = {"model": MODEL, "max_tokens": MAX_TOKENS, "messages": msgs}
        if system:
            kwargs["system"] = system
        resp = _client.messages.create(**kwargs)
        return resp.content[0].text.strip()
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return f"[Error: {e}]"


def analyze_trade_setup(symbol: str, direction: str, confidence: int,
                        conditions: dict, rrr: float) -> str:
    prompt = (
        f"ICT trade setup on {symbol}:\n"
        f"Direction: {direction}\n"
        f"RRR: {rrr:.2f}\n"
        f"Confidence: {confidence}/7\n"
        f"Conditions: {conditions}\n\n"
        "In 2-3 sentences, assess this setup quality and highlight any concerns."
    )
    return _ask(prompt, system="You are an expert ICT forex analyst. Be concise.")


def explain_evolution(param: str, old_val, new_val,
                      wr_before: float, wr_after: float, kept: bool) -> str:
    action = "KEPT" if kept else "REVERTED"
    prompt = (
        f"Strategy evolution result:\n"
        f"Parameter: {param}\n"
        f"Change: {old_val} -> {new_val}\n"
        f"Win rate: {wr_before:.1%} -> {wr_after:.1%}\n"
        f"Decision: {action}\n\n"
        "In one sentence, explain why this result makes sense."
    )
    return _ask(prompt, system="You are a quant strategy analyst. Be brief.")


def sunday_review(stats: dict) -> str:
    prompt = (
        f"Weekly trading performance review:\n"
        f"Trades: {stats.get('total_trades', 0)}\n"
        f"Win rate: {stats.get('win_rate', 0):.1%}\n"
        f"Avg RRR: {stats.get('avg_rrr', 0):.2f}\n"
        f"Max drawdown: {stats.get('max_dd', 0):.2f}%\n"
        f"Best pair: {stats.get('best_pair', 'N/A')}\n\n"
        "Provide 3 specific recommendations for next week's trading."
    )
    return _ask(prompt, system="You are an algorithmic trading coach.")
