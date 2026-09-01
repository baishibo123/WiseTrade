"""
Position episodes: the fragmentation-invariant unit of account for trade stats.

A *fill* is one BUY or SELL execution. An *episode* is the interval from a
symbol's position going 0 -> nonzero -> back to 0. Every add and trim in between
rolls up into one record.

Why this exists: fill-based trade statistics can be moved arbitrarily by how an
exit is sliced, with no change to the economics. Buying 300 @ 10 and exiting half
at 11 and half at 9 nets exactly zero PnL, yet:

    exit as 150@11, 150@9      -> 2 fills, 1 "win"  -> 50.00% fill win rate
    exit as 50@11 x3, 150@9    -> 4 fills, 3 "wins" -> 75.00%
    exit as 10@11 x15, 150@9   -> 16 fills, 15 wins -> 93.75%

All three are one episode with PnL 0 — not a win — in every slicing. Splitting an
exit changes `exit_fills` and nothing else. Nor is this adversarial: any strategy
that scales out of winners and dumps losers whole produces it naturally.

Returns-based metrics (Sharpe, max drawdown, CAGR) are computed from the
mark-to-market equity curve and are immune to all of this. Episode statistics are
diagnostics; the equity curve is ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class PositionEpisode:
    """One 0 -> nonzero -> 0 interval for a single symbol."""

    symbol: str
    opened_at: int                       # ms, the fill that took position off zero
    closed_at: Optional[int] = None      # ms, the fill that returned it to zero
    entry_fills: int = 0                 # diagnostic only — never a denominator
    exit_fills: int = 0                  # diagnostic only — never a denominator
    shares_in: float = 0.0
    shares_out: float = 0.0
    notional_in: float = 0.0             # capital committed across all entries
    notional_out: float = 0.0
    realized_pnl: float = 0.0
    is_open: bool = True

    @property
    def is_win(self) -> bool:
        """Profitable on realized PnL. Open episodes are never counted as wins."""
        return (not self.is_open) and self.realized_pnl > 0

    def duration_ms(self, until: Optional[int] = None) -> int:
        """Time held. Open episodes measure to `until` (normally the last bar)."""
        end = self.closed_at if self.closed_at is not None else until
        return 0 if end is None else max(0, end - self.opened_at)


def build_episodes(trades: list[dict], zero_tol: float = 1e-9) -> list[PositionEpisode]:
    """
    Roll a chronological fill log up into position episodes.

    Input:  Portfolio._trades — dicts of {datetime, symbol, action, shares, price, pnl}
    Output: one PositionEpisode per 0 -> nonzero -> 0 interval, in open order.

    Pure function over the fill log: it replays a running share count per symbol
    and needs no portfolio state, which is what keeps it independently testable.
    Long-only — a SELL with no open position is ignored rather than opening a
    short, matching what Portfolio actually permits.
    """
    running: dict[str, float] = {}
    current: dict[str, PositionEpisode] = {}
    episodes: list[PositionEpisode] = []

    for fill in sorted(trades, key=lambda t: t["datetime"]):
        sym = fill["symbol"]
        shares = float(fill["shares"])
        price = float(fill["price"])
        held = running.get(sym, 0.0)

        if fill["action"] == "BUY":
            if sym not in current:
                current[sym] = PositionEpisode(symbol=sym, opened_at=fill["datetime"])
                episodes.append(current[sym])
            ep = current[sym]
            ep.entry_fills += 1
            ep.shares_in += shares
            ep.notional_in += shares * price
            running[sym] = held + shares

        elif fill["action"] == "SELL":
            ep = current.get(sym)
            if ep is None:                      # long-only: nothing to close
                continue
            ep.exit_fills += 1
            ep.shares_out += shares
            ep.notional_out += shares * price
            ep.realized_pnl += float(fill.get("pnl") or 0.0)
            held = running[sym] = held - shares

            # Tolerance is relative: shares are floats (fractional sizing), so an
            # exact == 0.0 comparison would leave episodes spuriously open.
            if abs(held) <= zero_tol * max(1.0, ep.shares_in):
                ep.closed_at = fill["datetime"]
                ep.is_open = False
                running[sym] = 0.0
                del current[sym]

    return episodes


def episode_stats(episodes: list[PositionEpisode], last_timestamp: Optional[int] = None) -> dict[str, Any]:
    """
    Summarise episodes into the diagnostic block of the metrics dict.

    Three win rates are reported because they answer three different questions
    and disagreeing with each other is itself informative — see the annotations
    on each key below.
    """
    closed = [e for e in episodes if not e.is_open]
    open_ = [e for e in episodes if e.is_open]
    wins = [e for e in closed if e.is_win]
    losses = [e for e in closed if e.realized_pnl < 0]

    n = len(closed)
    total_notional = sum(e.notional_in for e in closed)
    total_time = sum(e.duration_ms(last_timestamp) for e in closed)

    # How often was I right? Each episode counts once, regardless of size or
    # duration. Fragmentation-invariant, but size-blind: nine tiny wins and one
    # huge loss still reads 90%.
    wr_count = (len(wins) / n * 100) if n else 0.0

    # What share of the capital I committed went into positions that made money?
    # Weighted by notional_in, so a large losing position drags this down in
    # proportion to the capital it consumed.
    wr_notional = (sum(e.notional_in for e in wins) / total_notional * 100) if total_notional else 0.0

    # What share of my time in the market was spent in positions that ended
    # profitably? Weighted by holding period, so it penalises losers held long
    # and rewards nothing for a winner exited instantly.
    wr_time = (sum(e.duration_ms(last_timestamp) for e in wins) / total_time * 100) if total_time else 0.0

    gross_win = sum(e.realized_pnl for e in wins)
    gross_loss = abs(sum(e.realized_pnl for e in losses))

    return {
        "num_episodes": n,
        "num_open_episodes": len(open_),
        "num_wins": len(wins),
        "num_losses": len(losses),

        "win_rate_count_pct": round(wr_count, 2),
        "win_rate_notional_pct": round(wr_notional, 2),
        "win_rate_time_pct": round(wr_time, 2),
        # count minus notional. Strongly positive means many small winners and
        # few large losers — the divergence is the tell, not either number alone.
        "win_rate_size_skew_pct": round(wr_count - wr_notional, 2),

        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else 0.0,
        "avg_win": round(gross_win / len(wins), 2) if wins else 0.0,
        "avg_loss": round(gross_loss / len(losses), 2) if losses else 0.0,
        "largest_win": round(max((e.realized_pnl for e in wins), default=0.0), 2),
        "largest_loss": round(min((e.realized_pnl for e in losses), default=0.0), 2),

        # Fragmentation diagnostics. These are why the fill-level counters were
        # dropped rather than kept alongside: the information is still here.
        "num_fills": sum(e.entry_fills + e.exit_fills for e in episodes),
        "avg_entry_fills": round(sum(e.entry_fills for e in closed) / n, 2) if n else 0.0,
        "avg_exit_fills": round(sum(e.exit_fills for e in closed) / n, 2) if n else 0.0,
        "avg_episode_minutes": round(total_time / n / 60_000, 1) if n else 0.0,
        "open_notional": round(sum(e.notional_in for e in open_), 2),
    }
