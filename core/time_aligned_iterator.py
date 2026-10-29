"""
Time-aligned iterator for multi-symbol bar synchronization
Merges multiple symbol feeds into chronological event stream
"""

from typing import Dict, Iterator, Optional, Tuple
from collections import defaultdict
from database.schema import Bar
import logging


class TimeAlignedIterator:
    """
    Synchronizes multiple symbol feeds into a single time-ordered stream.

    Handles:
    - Missing bars via forward-fill (uses last known price)
    - Different start times across symbols
    - Memory-efficient streaming (doesn't load all data into memory)

    Usage:
        feeds = {
            "AAPL": SQLiteFeed("AAPL", start, end),
            "MSFT": SQLiteFeed("MSFT", start, end)
        }

        iterator = TimeAlignedIterator(feeds)
        for timestamp, bars in iterator:
            # bars = {"AAPL": Bar(...), "MSFT": Bar(...)}
            process(bars)
    """

    def __init__(self, feeds: Dict[str, Iterator[Bar]]):
        """
        Initialize time-aligned iterator

        Args:
            feeds: Dictionary mapping symbol -> bar iterator
        """
        self.symbols = list(feeds.keys())

        # Initialize iterators from feeds
        self.feed_iterators = {}
        for symbol, feed in feeds.items():
            self.feed_iterators[symbol] = iter(feed)  # ← KEY FIX: Call iter() first!

        # Buffer: holds next bar for each symbol
        self.buffers: Dict[str, Optional[Bar]] = {}

        # Cache: last known bar for forward-fill
        self.last_known_bars: Dict[str, Bar] = {}

        # Statistics
        self.total_timestamps = 0
        self.ffill_count = defaultdict(int)

        # Initialize buffers with first bar from each feed
        self._initialize_buffers()

        logging.info(f"TimeAlignedIterator initialized with {len(self.symbols)} symbols")

    def _initialize_buffers(self):
        """Load first bar from each feed into buffers"""
        for symbol in self.symbols:
            try:
                # Get first bar from iterator
                first_bar = next(self.feed_iterators[symbol], None)
                self.buffers[symbol] = first_bar

                if first_bar:
                    self.last_known_bars[symbol] = first_bar
                else:
                    logging.warning(f"No bars available for {symbol}")

            except Exception as e:
                logging.error(f"Failed to initialize feed for {symbol}: {e}")
                self.buffers[symbol] = None


    def __iter__(self):
        """
        Iterate through time-synchronized bars

        Yields:
            Tuple[int, Dict[str, Bar]]: (timestamp, {symbol: bar})
        """
        while self._has_data():
            # 1. Find earliest timestamp across all buffers
            min_timestamp = self._get_min_timestamp()

            if min_timestamp is None:
                break

            # 2. Collect all bars at this timestamp
            bars_at_timestamp = self._collect_bars_at_timestamp(min_timestamp)

            # 3. Yield synchronized bars
            self.total_timestamps += 1
            yield min_timestamp, bars_at_timestamp

        # Log statistics
        self._log_statistics()

    def _has_data(self) -> bool:
        """Check if any symbol still has data"""
        return any(bar is not None for bar in self.buffers.values())

    def _get_min_timestamp(self) -> Optional[int]:
        """Find earliest timestamp among current buffers"""
        valid_timestamps = [
            bar.datetime for bar in self.buffers.values()
            if bar is not None
        ]

        return min(valid_timestamps) if valid_timestamps else None

    def _collect_bars_at_timestamp(self, target_timestamp: int) -> Dict[str, Bar]:
        """
        Collect bars for all symbols at target timestamp
        Uses forward-fill for missing bars
        """
        bars = {}

        for symbol in self.symbols:
            current_bar = self.buffers[symbol]

            if current_bar is not None and current_bar.datetime == target_timestamp:
                # Real bar exists at this timestamp
                bars[symbol] = current_bar
                self.last_known_bars[symbol] = current_bar

                # Advance buffer to next bar
                self.buffers[symbol] = next(self.feed_iterators[symbol], None)

            elif symbol in self.last_known_bars:
                # Forward-fill: create synthetic bar with aligned timestamp
                bars[symbol] = self._create_forward_fill_bar(
                    self.last_known_bars[symbol],
                    target_timestamp
                )
                self.ffill_count[symbol] += 1

            # else: symbol has no data yet, skip it

        return bars

    def _create_forward_fill_bar(self, last_bar: Bar, new_timestamp: int) -> Bar:
        """
        Create synthetic bar by forward-filling last known price

        Key: timestamp is aligned with current processing time,
        preventing downstream bugs from stale timestamps
        """
        return Bar(
            symbol=last_bar.symbol,
            datetime=new_timestamp,  # ← CRITICAL: Use current timestamp
            open=last_bar.close,
            high=last_bar.close,
            low=last_bar.close,
            close=last_bar.close,
            volume=0  # Mark as synthetic (zero volume)
        )

    def _log_statistics(self):
        """Log forward-fill statistics"""
        if self.ffill_count:
            logging.info(f"TimeAlignedIterator processed {self.total_timestamps:,} timestamps")
            for symbol, count in self.ffill_count.items():
                pct = (count / self.total_timestamps) * 100
                logging.info(f"  {symbol}: {count:,} forward-fills ({pct:.2f}%)")


# ============================================================================
# Testing utility
# ============================================================================

def test_time_aligned_iterator():
    """
    Simple test to verify TimeAlignedIterator behavior
    Run this to validate the implementation
    """
    from database.schema import Bar

    # Mock feeds with different timing
    def mock_feed_aapl():
        yield Bar("AAPL", 100, 150.0, 151.0, 149.0, 150.5, 1000)
        yield Bar("AAPL", 101, 150.5, 152.0, 150.0, 151.0, 1100)
        # Missing bar at t=102
        yield Bar("AAPL", 103, 151.0, 151.5, 150.5, 151.2, 1050)

    def mock_feed_msft():
        # Starts later
        yield Bar("MSFT", 101, 300.0, 301.0, 299.0, 300.5, 2000)
        yield Bar("MSFT", 102, 300.5, 302.0, 300.0, 301.0, 2100)
        yield Bar("MSFT", 103, 301.0, 301.5, 300.5, 301.2, 2050)

    feed_iterators = {
        "AAPL": mock_feed_aapl(),
        "MSFT": mock_feed_msft()
    }

    iterator = TimeAlignedIterator(feed_iterators)

    print("\n=== TimeAlignedIterator Test ===")
    for timestamp, bars in iterator:
        print(f"\nTimestamp {timestamp}:")
        for symbol, bar in bars.items():
            ffill_marker = " [FFILL]" if bar.volume == 0 else ""
            print(f"  {symbol}: close=${bar.close:.2f}, vol={bar.volume}{ffill_marker}")

    print("\n✓ Test complete")


if __name__ == "__main__":
    test_time_aligned_iterator()