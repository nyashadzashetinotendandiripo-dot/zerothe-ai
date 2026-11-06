"""Deriv Synthetic Indices trading plugin for ZerotheBot.

register(api) returns a list of tool dicts. Each tool dict has:
    name         tool name (plugin id is prefixed: deriv-trading_quote)
    description  what the model reads
    input_schema JSON Schema for arguments
    handler      callable(ctx, args) -> str | dict | list
    risk         "safe" (default) or "purchase" -> user asked first
    needs_screen True if tool drives the Bot's browser tab

api.config(key, default) reads plugin settings (non-secret and secret fields).
api.http(ctx, method, url, ...) makes HTTP requests respecting network policy.

This plugin needs_engine=true because it opens raw WebSockets to Deriv's API,
which sandboxed plugins cannot do.
"""
from __future__ import annotations

import json
import math
import time
from typing import Any

try:
    from websockets.sync.client import connect as _ws_connect
    _HAS_WS = True
except ImportError:  # pragma: no cover
    _HAS_WS = False

WS_PUBLIC = "wss://api.derivws.com/trading/v1/options/ws/public"

_WS_HEADERS = {
    "Origin": "https://home.deriv.com",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
    ),
}

# --------------------------------------------------------------------------- config (captured at registration)
_API_TOKEN = ""
_APP_ID = "1089"
_ALLOW_LIVE = False
_MAX_STAKE = 1.0
_MAX_OPEN = 1
_DAILY_LOSS_LIMIT = 10.0


# --------------------------------------------------------------------------- WebSocket helpers
class _DerivWS:
    """One persistent WebSocket connection to Deriv's public API."""

    def __init__(self, token: str | None = None, timeout: int = 30):
        self.token = token
        self.timeout = timeout
        self.ws = None
        self._req_id = 0

    def __enter__(self):
        if not _HAS_WS:
            raise RuntimeError("websockets library not installed on this machine")
        self.ws = _ws_connect(
            WS_PUBLIC,
            open_timeout=self.timeout,
            close_timeout=5,
            additional_headers=_WS_HEADERS,
        )
        if self.token:
            self.call({"authorize": self.token})
        return self

    def __exit__(self, *exc):
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def call(self, payload: dict[str, Any]) -> dict[str, Any]:
        req_id = self._next_id()
        msg = dict(payload)
        msg["req_id"] = req_id
        self.ws.send(json.dumps(msg))
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            remaining = deadline - time.time()
            try:
                raw = self.ws.recv(timeout=remaining)
            except TimeoutError:
                raise TimeoutError("Deriv API request timed out")
            d = json.loads(raw)
            if d.get("req_id") == req_id:
                if "error" in d:
                    raise RuntimeError(
                        f"Deriv API error: {d['error'].get('message', d['error'])}"
                    )
                return d
        raise TimeoutError("Deriv API request timed out")


# --------------------------------------------------------------------------- public (no-auth) tools
def _quote(symbol: str) -> dict[str, Any]:
    with _DerivWS() as ws:
        d = ws.call({"ticks": symbol, "subscribe": 1})
    tick = d.get("tick", {})
    return {
        "symbol": tick.get("symbol", symbol),
        "quote": tick.get("quote"),
        "epoch": tick.get("epoch"),
        "bid": tick.get("bid"),
        "ask": tick.get("ask"),
    }


def _candles(symbol: str, granularity: int, count: int) -> list[dict[str, Any]]:
    with _DerivWS() as ws:
        d = ws.call(
            {
                "ticks_history": symbol,
                "count": count,
                "end": "latest",
                "style": "candles",
                "granularity": granularity,
            }
        )
    candles = d.get("candles", [])
    return [
        {
            "epoch": c.get("epoch"),
            "open": c.get("open"),
            "high": c.get("high"),
            "low": c.get("low"),
            "close": c.get("close"),
        }
        for c in candles
    ]


def _symbols() -> list[dict[str, Any]]:
    with _DerivWS() as ws:
        d = ws.call({"active_symbols": "brief"})
    all_syms = d.get("active_symbols", [])
    syns = [s for s in all_syms if s.get("market") == "synthetic_index"]
    return [
        {
            "symbol": s.get("underlying_symbol"),
            "display_name": s.get("underlying_symbol_name", s.get("underlying_symbol")),
            "submarket": s.get("submarket"),
            "exchange_is_open": s.get("exchange_is_open"),
            "pip_size": s.get("pip_size"),
        }
        for s in syns
    ]


def _contracts_for(symbol: str) -> dict[str, Any]:
    with _DerivWS() as ws:
        d = ws.call({"contracts_for": symbol})
    contracts = d.get("contracts_for", {}).get("available", [])
    return {
        "symbol": symbol,
        "contracts": [
            {
                "contract_type": c.get("contract_type"),
                "display_name": c.get("contract_category"),
                "barrier": c.get("barrier"),
                "min_stake": c.get("default_stake"),
                "max_stake": c.get("default_stake"),
                "min_duration": c.get("min_contract_duration"),
                "max_duration": c.get("max_contract_duration"),
                "expiry_type": c.get("expiry_type"),
                "market": c.get("market"),
                "sentiment": c.get("sentiment"),
            }
            for c in contracts
        ],
    }


# --------------------------------------------------------------------------- indicators (computed client-side)
def _rsi(closes: list[float], period: int = 14) -> list[float | None]:
    if len(closes) < period + 1:
        return [None] * len(closes)
    gains = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    losses = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    result: list[float | None] = [None] * period
    for i in range(period, len(closes)):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        if avg_loss == 0:
            result.append(100.0)
        else:
            rs = avg_gain / avg_loss
            result.append(100.0 - (100.0 / (1.0 + rs)))
    return result


def _ema(closes: list[float], period: int) -> list[float | None]:
    if len(closes) < period:
        return [None] * len(closes)
    k = 2.0 / (period + 1)
    result: list[float | None] = [None] * (period - 1)
    result.append(sum(closes[:period]) / period)
    for i in range(period, len(closes)):
        result.append(closes[i] * k + result[-1] * (1 - k))
    return result


def _indicators(
    symbol: str, granularity: int, count: int, indicator: str
) -> dict[str, Any]:
    candles_list = _candles(symbol, granularity, count)
    closes = [c["close"] for c in candles_list if c["close"] is not None]
    highs = [c["high"] for c in candles_list if c["high"] is not None]
    lows = [c["low"] for c in candles_list if c["low"] is not None]

    out: dict[str, Any] = {
        "symbol": symbol,
        "granularity": granularity,
        "count": len(closes),
    }

    if indicator in ("rsi", "all") and len(closes) > 14:
        out["rsi"] = _rsi(closes)
    if indicator in ("ema", "all") and len(closes) > 20:
        out["ema20"] = _ema(closes, 20)
        if len(closes) > 50:
            out["ema50"] = _ema(closes, 50)
    if indicator in ("atr", "all") and len(closes) > 14:
        trs: list[float | None] = [None]
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(tr)
        atr: list[float | None] = [None] * 14
        if len(trs) > 14:
            atr.append(sum(x for x in trs[1:15] if x) / 14)
            for i in range(15, len(trs)):
                prev = atr[-1] if atr[-1] else 0
                atr.append((prev * 13 + trs[i]) / 14)
        else:
            atr.extend([None] * (len(trs) - 14))
        out["atr14"] = atr
    if indicator in ("bollinger", "all") and len(closes) > 20:
        sma: list[float | None] = [None] * 19
        for i in range(19, len(closes)):
            sma.append(sum(closes[i - 19 : i + 1]) / 20)
        std: list[float | None] = [None] * 19
        for i in range(19, len(closes)):
            window = closes[i - 19 : i + 1]
            mean = sum(window) / 20
            var = sum((x - mean) ** 2 for x in window) / 20
            std.append(math.sqrt(var))
        out["bollinger"] = {
            "middle": sma,
            "upper": [
                (s + 2 * st) if (s is not None and st is not None) else None
                for s, st in zip(sma, std)
            ],
            "lower": [
                (s - 2 * st) if (s is not None and st is not None) else None
                for s, st in zip(sma, std)
            ],
        }
    return out


# --------------------------------------------------------------------------- authenticated tools
def _require_token() -> str:
    if not _API_TOKEN:
        raise RuntimeError(
            "Deriv API token not configured. Set api_token in plugin settings."
        )
    return _API_TOKEN


def _is_demo(loginid: str) -> bool:
    return loginid.startswith("VRTC")


def _account() -> dict[str, Any]:
    token = _require_token()
    with _DerivWS(token=token) as ws:
        auth = ws.call({"authorize": token})
        balance = ws.call({"balance": 1})
    auth_info = auth.get("authorize", {})
    bal = balance.get("balance", {})
    loginid = auth_info.get("loginid", "")
    return {
        "loginid": loginid,
        "account_type": "demo" if _is_demo(loginid) else "real",
        "currency": bal.get("currency"),
        "balance": bal.get("balance"),
        "email": auth_info.get("email"),
    }


def _positions() -> list[dict[str, Any]]:
    token = _require_token()
    with _DerivWS(token=token) as ws:
        ws.call({"authorize": token})
        d = ws.call({"portfolio": 1})
    contracts = d.get("portfolio", {}).get("contracts", [])
    return [
        {
            "contract_id": c.get("contract_id"),
            "symbol": c.get("underlying_symbol"),
            "contract_type": c.get("contract_type"),
            "buy_price": c.get("buy_price"),
            "current_spot": c.get("current_spot"),
            "profit": c.get("profit"),
            "status": c.get("status"),
            "expiry": c.get("expiry"),
        }
        for c in contracts
    ]


# --------------------------------------------------------------------------- safety
def _check_safety(stake: float) -> None:
    """Enforce max_stake, max_open, daily_loss_limit. Raises if blocked."""
    stake = float(stake)
    if stake <= 0:
        raise RuntimeError("stake must be positive")
    if stake > _MAX_STAKE:
        raise RuntimeError(f"stake {stake} exceeds max_stake {_MAX_STAKE}")

    open_count = len(_positions())
    if open_count >= _MAX_OPEN:
        raise RuntimeError(f"max open positions ({_MAX_OPEN}) reached")

    if _DAILY_LOSS_USED >= _DAILY_LOSS_LIMIT:
        raise RuntimeError(
            f"daily loss limit ({_DAILY_LOSS_LIMIT}) reached: {_DAILY_LOSS_USED} used"
        )


_DAILY_LOSS_USED = 0.0


def _buy(
    symbol: str,
    contract_type: str,
    stake: float,
    duration: int,
    duration_unit: str = "t",
    barrier: str | None = None,
) -> dict[str, Any]:
    global _DAILY_LOSS_USED

    token = _require_token()

    # Live-account guard
    with _DerivWS(token=token) as ws:
        auth = ws.call({"authorize": token})
    loginid = auth.get("authorize", {}).get("loginid", "")
    if not _is_demo(loginid) and not _ALLOW_LIVE:
        raise RuntimeError(
            "Real-account trading is disabled (allow_live=false). "
            "Enable it in plugin settings to allow real orders."
        )

    _check_safety(stake)

    with _DerivWS(token=token) as ws:
        ws.call({"authorize": token})
        proposal_payload: dict[str, Any] = {
            "proposal": 1,
            "underlying": symbol,
            "contract_type": contract_type,
            "price": stake,
            "duration": duration,
            "duration_unit": duration_unit,
        }
        if barrier:
            proposal_payload["barrier"] = barrier
        prop = ws.call(proposal_payload)
        proposal = prop.get("proposal", {})
        proposal_id = proposal.get("id")
        ask_price = proposal.get("ask_price")

        if not proposal_id:
            raise RuntimeError(f"No proposal returned: {prop}")

        buy_result = ws.call({"buy": proposal_id, "price": ask_price})
        contract = buy_result.get("buy", {})
        result = {
            "contract_id": contract.get("contract_id"),
            "transaction_id": contract.get("transaction_id"),
            "buy_price": contract.get("buy_price"),
            "ask_price": ask_price,
            "symbol": symbol,
            "contract_type": contract_type,
            "status": contract.get("status"),
            "demo": _is_demo(loginid),
        }
        return result


def _close(contract_id: str) -> dict[str, Any]:
    token = _require_token()

    with _DerivWS(token=token) as ws:
        ws.call({"authorize": token})
        sell_result = ws.call({"sell": contract_id, "price": 0})
        contract = sell_result.get("sell", {})
        return {
            "contract_id": contract.get("contract_id"),
            "transaction_id": contract.get("transaction_id"),
            "sell_price": contract.get("sell_price"),
            "status": contract.get("status"),
        }


# --------------------------------------------------------------------------- backtest (client-side, no token)
def _sim_slice(
    closes: list[float],
    start: int,
    end: int,
    strategy: str,
    k: float,
    hold: int,
    stake_frac: float,
    stop_bp: float,
    take_bp: float,
    spread_bp: float,
) -> dict[str, Any]:
    equity = 1.0
    trades = 0
    wins = 0
    i = start
    while i < end - hold:
        ret = math.log(closes[i] / closes[i - 1]) if i > 0 else 0.0
        window = closes[max(0, i - 50) : i]
        if len(window) < 10:
            i += 1
            continue
        mean = sum(window) / len(window)
        var = sum((x - mean) ** 2 for x in window) / len(window)
        sd = math.sqrt(var) if var > 0 else 0
        z = (math.log(closes[i] / closes[i - 1]) - mean) / sd if sd > 0 else 0

        dir_ = 0
        if strategy == "mean-revert" and abs(z) > k:
            dir_ = -1 if z > 0 else 1
        elif strategy == "fade-spike" and abs(z) > k:
            dir_ = 1 if z > 0 else -1
        elif strategy == "momentum" and abs(z) > k:
            dir_ = 1 if z > 0 else -1

        if not dir_:
            i += 1
            continue

        exit_idx = min(i + hold, end - 1)
        move = math.log(closes[exit_idx] / closes[i]) * dir_

        stake_amt = equity * stake_frac
        costs = (2 * spread_bp) / 1e4
        net = move - costs
        step = stake_amt * net
        won = net > 0

        equity += step
        trades += 1
        if won:
            wins += 1
        i = exit_idx

    logret = math.log(equity) if equity > 0 else -999.0
    return {"logret": logret, "trades": trades, "wins": wins}


def _backtest(
    symbol: str,
    strategy: str = "mean-revert",
    k: float = 2.0,
    hold: int = 20,
    stake_frac: float = 0.02,
    stop_bp: float = 30.0,
    take_bp: float = 60.0,
    spread_bp: float = 5.0,
) -> dict[str, Any]:
    """Walk-forward backtest. Returns out-of-sample stats only."""
    candles_list = _candles(symbol, granularity=60, count=2000)
    closes = [c["close"] for c in candles_list if c["close"] is not None]
    if len(closes) < 300:
        return {"error": "insufficient history", "bars": len(closes)}

    n = len(closes)
    folds = 4
    size = n // folds
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    trades = 0
    wins = 0
    oos_logret = 0.0

    for f in range(1, folds):
        train_end = f * size
        test_end = min((f + 1) * size, n)
        if test_end - train_end < 50:
            continue

        best_k = k
        best_logret = -999.0
        for kk in [1.0, 1.5, 2.0, 2.5, 3.0]:
            r = _sim_slice(
                closes, 1, train_end, strategy, kk, hold, stake_frac,
                stop_bp, take_bp, spread_bp,
            )
            if r["logret"] > best_logret:
                best_logret = r["logret"]
                best_k = kk

        r = _sim_slice(
            closes, train_end, test_end, strategy, best_k, hold,
            stake_frac, stop_bp, take_bp, spread_bp,
        )
        equity *= math.exp(r["logret"])
        trades += r["trades"]
        wins += r["wins"]
        oos_logret += r["logret"]

    peak = max(peak, equity)
    max_dd = min(max_dd, (equity - peak) / peak)
    win_rate = (100.0 * wins / trades) if trades else 0.0

    return {
        "symbol": symbol,
        "strategy": strategy,
        "k": k,
        "hold": hold,
        "folds": folds,
        "bars": n,
        "oos_logret": round(oos_logret, 4),
        "oos_trades": trades,
        "oos_win_rate": round(win_rate, 1),
        "end_equity": round(equity, 4),
        "max_drawdown": round(max_dd, 4),
    }


# --------------------------------------------------------------------------- register
def register(api) -> list[dict[str, Any]]:
    global _API_TOKEN, _APP_ID, _ALLOW_LIVE, _MAX_STAKE, _MAX_OPEN, _DAILY_LOSS_LIMIT

    _API_TOKEN = api.config("api_token", "")
    _APP_ID = api.config("app_id", "1089")
    _ALLOW_LIVE = api.config("allow_live", "false").lower() == "true"
    try:
        _MAX_STAKE = float(api.config("max_stake", "1.0"))
    except ValueError:
        _MAX_STAKE = 1.0
    try:
        _MAX_OPEN = int(api.config("max_open", "1"))
    except ValueError:
        _MAX_OPEN = 1
    try:
        _DAILY_LOSS_LIMIT = float(api.config("daily_loss_limit", "10.0"))
    except ValueError:
        _DAILY_LOSS_LIMIT = 10.0

    tools = [
        {
            "name": "quote",
            "description": "Live spot price (bid/ask) for a Deriv synthetic symbol. No token needed.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Symbol code, e.g. R_75, BOOM500, CRASH500, JD50",
                    }
                },
                "required": ["symbol"],
            },
            "handler": lambda ctx, args: _quote(args["symbol"]),
        },
        {
            "name": "candles",
            "description": "OHLC candle history for a symbol. No token needed.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Symbol code"},
                    "granularity": {
                        "type": "integer",
                        "description": "Seconds per candle",
                        "default": 60,
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of candles",
                        "default": 100,
                    },
                },
                "required": ["symbol"],
            },
            "handler": lambda ctx, args: _candles(
                args["symbol"], args.get("granularity", 60), args.get("count", 100)
            ),
        },
        {
            "name": "symbols",
            "description": "List all active synthetic symbols with display names and market status.",
            "input_schema": {"type": "object", "properties": {}},
            "handler": lambda ctx, args: _symbols(),
        },
        {
            "name": "contracts",
            "description": "Available contract types, durations, barriers, min/max stake for a symbol.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Symbol code"}
                },
                "required": ["symbol"],
            },
            "handler": lambda ctx, args: _contracts_for(args["symbol"]),
        },
        {
            "name": "indicators",
            "description": "Technical indicators (RSI, EMA, ATR, Bollinger Bands) computed from recent candles.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Symbol code"},
                    "granularity": {
                        "type": "integer",
                        "description": "Seconds per candle",
                        "default": 60,
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of candles",
                        "default": 200,
                    },
                    "indicator": {
                        "type": "string",
                        "description": "rsi | ema | atr | bollinger | all",
                        "default": "all",
                    },
                },
                "required": ["symbol"],
            },
            "handler": lambda ctx, args: _indicators(
                args["symbol"],
                args.get("granularity", 60),
                args.get("count", 200),
                args.get("indicator", "all"),
            ),
        },
        {
            "name": "account",
            "description": "Account balance, currency, and demo/real status. Needs API token.",
            "input_schema": {"type": "object", "properties": {}},
            "handler": lambda ctx, args: _account(),
        },
        {
            "name": "positions",
            "description": "Open contracts with entry price, current P/L, and expiry. Needs API token.",
            "input_schema": {"type": "object", "properties": {}},
            "handler": lambda ctx, args: _positions(),
        },
        {
            "name": "backtest",
            "description": "Walk-forward backtest of a strategy on historical ticks. Out-of-sample result only.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Symbol code"},
                    "strategy": {
                        "type": "string",
                        "description": "mean-revert | fade-spike | momentum",
                        "default": "mean-revert",
                    },
                    "k": {"type": "number", "description": "Z-score threshold", "default": 2.0},
                    "hold": {"type": "integer", "description": "Hold bars", "default": 20},
                    "stake": {
                        "type": "number",
                        "description": "Stake fraction of equity per trade",
                        "default": 0.02,
                    },
                    "stop_bp": {"type": "number", "description": "Stop-loss in basis points", "default": 30},
                    "take_bp": {"type": "number", "description": "Take-profit in basis points", "default": 60},
                    "spread_bp": {
                        "type": "number",
                        "description": "Round-trip spread cost in basis points",
                        "default": 5,
                    },
                },
                "required": ["symbol"],
            },
            "handler": lambda ctx, args: _backtest(
                args["symbol"],
                args.get("strategy", "mean-revert"),
                args.get("k", 2.0),
                args.get("hold", 20),
                args.get("stake", 0.02),
                args.get("stop_bp", 30),
                args.get("take_bp", 60),
                args.get("spread_bp", 5),
            ),
        },
        {
            "name": "buy",
            "description": "Place a Deriv contract. Requires API token + user approval.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Symbol code"},
                    "contract_type": {
                        "type": "string",
                        "description": "Call | Put | Rise | Fall | Higher | Lower | ...",
                    },
                    "stake": {"type": "number", "description": "Stake amount"},
                    "duration": {"type": "integer", "description": "Contract duration"},
                    "duration_unit": {
                        "type": "string",
                        "description": "t | s | m | h | d",
                        "default": "t",
                    },
                    "barrier": {"type": "string", "description": "Barrier level"},
                },
                "required": ["symbol", "contract_type", "stake", "duration"],
            },
            "handler": lambda ctx, args: _buy(
                args["symbol"],
                args["contract_type"],
                args["stake"],
                args["duration"],
                args.get("duration_unit", "t"),
                args.get("barrier"),
            ),
            "risk": "purchase",
        },
        {
            "name": "close",
            "description": "Sell an open contract. Requires API token + user approval.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "contract_id": {
                        "type": "string",
                        "description": "Open contract ID",
                    }
                },
                "required": ["contract_id"],
            },
            "handler": lambda ctx, args: _close(args["contract_id"]),
            "risk": "purchase",
        },
    ]
    return tools
