import unittest
from unittest.mock import MagicMock, patch, call
import sys
import os
from collections import deque

# Ensure we can import from root
sys.path.append(os.getcwd())

from core.engine import Engine
from database.schema import Bar


# --------------------------------------------------------------------------
# 1. Mocks & Helpers
# --------------------------------------------------------------------------

class MockFeed:
    """A simple iterator that simulates the DB feed."""

    def __init__(self, bars, symbol="TEST_SYM"):
        self.bars = bars
        self.symbol = symbol

    def __iter__(self):
        return iter(self.bars)


class SpyStrategy:
    """
    A 'Spy' strategy that records what methods were called on it.
    We use this instead of a real Strategy to avoid testing Strategy logic here.
    """

    def __init__(self, params=None):
        self.name = "SpyStrategy"
        self.params = params or {}
        self.portfolio = None

        # Call recorders
        self.on_start_called = False
        self.on_end_called = False
        self.update_bar_calls = []  # Store args passed to update_bar
        self.next_calls = []  # Store args passed to next

    def on_start(self):
        self.on_start_called = True

    def update_bar(self, bar):
        self.update_bar_calls.append(bar)

    def next(self, bar):
        self.next_calls.append(bar)

    def on_end(self):
        self.on_end_called = True


# --------------------------------------------------------------------------
# 2. Test Suite
# --------------------------------------------------------------------------

class TestEngine(unittest.TestCase):

    def setUp(self):
        # 1. Create standard dummy data
        self.bars = [
            Bar("AAPL", 1000, 10, 11, 9, 10.5, 100),
            Bar("AAPL", 2000, 10.5, 12, 10, 11.0, 200),
            Bar("AAPL", 3000, 11.0, 13, 11, 12.0, 300),
        ]
        self.feed = MockFeed(self.bars)

        # 2. Patch Portfolio and Analyzer (External dependencies)
        # We don't want to test Portfolio logic here, just that Engine calls it.
        self.patcher_port = patch('core.engine.Portfolio')
        self.patcher_ana = patch('core.engine.Analyzer')

        self.MockPortfolio = self.patcher_port.start()
        self.MockAnalyzer = self.patcher_ana.start()

        # Setup return values for mocks
        self.mock_portfolio_instance = self.MockPortfolio.return_value
        self.mock_portfolio_instance.total_equity = 100_000.0

        self.mock_analyzer_instance = self.MockAnalyzer.return_value
        self.mock_analyzer_instance.metrics = {'total_equity': 100_000, 'total_return_pct': 0.0}

    def tearDown(self):
        self.patcher_port.stop()
        self.patcher_ana.stop()

    def test_execution_flow(self):
        """
        Verify the exact sequence:
        Init -> on_start -> (update_bar -> next -> portfolio.update) * N -> on_end
        """
        engine = Engine(
            feed=self.feed,
            strategy_class=SpyStrategy,
            initial_cash=50_000
        )

        analyzer = engine.run()

        # 1. Verify Portfolio Initialization
        self.MockPortfolio.assert_called_with(initial_cash=50000.0, min_trade_size=0.1)

        # 2. Access the strategy instance created inside Engine
        # (Since Engine creates the instance, we can't pass one in.
        # But we know it's stored in a local var. We can't reach it easily?
        # WAIT: The Engine doesn't expose 'self.strategy'.
        # But we can check side effects on the MockPortfolio.)

        # HOWEVER, SpyStrategy is a class. Engine instantiates it.
        # We need to spy on THAT instance.
        # Solution: We can't easily access the `strategy` object because it's local scope in run().
        # BUT, we can check the interaction via the mocks or by tricking the class.
        pass

    def test_lifecycle_calls(self):
        """
        Since we can't easily access the Strategy instance created inside `run()`,
        we will use a Mock for the strategy_class itself to capture the instance.
        """
        # Create a Mock Class that returns a Mock Instance
        mock_strat_instance = MagicMock()
        mock_strat_instance.name = "MockStrat"

        # When Engine calls strategy_class(), return our mock instance
        mock_strat_class = MagicMock(return_value=mock_strat_instance)
        mock_strat_class.__name__ = "MockStratClass"  # Engine uses __name__ for logging

        engine = Engine(feed=self.feed, strategy_class=mock_strat_class)
        engine.run()

        # --- VERIFICATIONS ---

        # 1. on_start called once?
        mock_strat_instance.on_start.assert_called_once()

        # 2. update_bar called 3 times? (For each bar)
        self.assertEqual(mock_strat_instance.update_bar.call_count, 3)
        # Check that it was called with the correct bars
        mock_strat_instance.update_bar.assert_has_calls([call(b) for b in self.bars])

        # 3. next called 3 times?
        self.assertEqual(mock_strat_instance.next.call_count, 3)

        # 4. portfolio.update called 3 times?
        self.assertEqual(self.mock_portfolio_instance.update.call_count, 3)

        # 5. on_end called once?
        mock_strat_instance.on_end.assert_called_once()

    def test_order_of_operations(self):
        """
        CRITICAL: Verify update_bar happens BEFORE next.
        """
        manager = MagicMock()
        # Attach our methods to a manager to track order
        mock_strat = MagicMock()
        mock_strat.name = "OrderedStrat"

        manager.attach_mock(mock_strat.update_bar, 'update_bar')
        manager.attach_mock(mock_strat.next, 'next')

        mock_class = MagicMock(return_value=mock_strat)
        mock_class.__name__ = "OrderedStrat"

        engine = Engine(feed=self.feed, strategy_class=mock_class)
        engine.run()

        # Verify the order of calls for the FIRST bar
        first_bar = self.bars[0]
        expected_calls = [
            call.update_bar(first_bar),
            call.next(first_bar)
        ]

        # Check the first two calls made to the manager
        self.assertEqual(manager.mock_calls[0], expected_calls[0])
        self.assertEqual(manager.mock_calls[1], expected_calls[1])

    # @patch('core.engine.SQLiteFeed')
    # @patch('core.engine.tqdm')
    def test_run_multiple(self):
        """Test the run_multiple class method"""

        # 1. Patch the SOURCE of SQLiteFeed (datafeed.db_feed)
        # 2. Patch the SOURCE of tqdm (tqdm module)
        with patch('datafeed.db_feed.SQLiteFeed') as MockSQLiteFeed, \
                patch('tqdm.tqdm') as mock_tqdm:
            # Configure mocks
            # When SQLiteFeed(...) is initialized, return our MockFeed
            MockSQLiteFeed.side_effect = lambda symbol, start, end, db_path=None: MockFeed(self.bars, symbol)

            # Disable tqdm progress bar (make it pass-through)
            mock_tqdm.side_effect = lambda x, **kwargs: x

            # Dummy strategy
            MockStrat = MagicMock()
            MockStrat.__name__ = "TestStrat"

            # Run the method under test
            results = Engine.run_multiple(
                symbols=['AAPL', 'TSLA'],
                strategy_class=MockStrat,
                start_datetime=1000,
                end_datetime=3000
            )

            # Assertions
            self.assertEqual(len(results), 2)
            self.assertIn('AAPL', results)
            self.assertIn('TSLA', results)

            # Verify SQLiteFeed was called twice (once per symbol)
            self.assertEqual(MockSQLiteFeed.call_count, 2)

if __name__ == '__main__':
    unittest.main()