"""
Technical indicator calculations for strategy use
Pure functions - no state, just calculations
"""

from typing import List, Optional, Tuple
import numpy as np


def calculate_sma(prices: List[float], period: int) -> Optional[float]:
    """
    Calculate Simple Moving Average

    Args:
        prices: List of prices (most recent last)
        period: Lookback period

    Returns:
        SMA value or None if insufficient data
    """
    if len(prices) < period:
        return None
    return float(np.mean(prices[-period:]))


def calculate_ema(prices: List[float], period: int) -> Optional[float]:
    """
    Calculate Exponential Moving Average

    Args:
        prices: List of prices (most recent last)
        period: Lookback period

    Returns:
        EMA value or None if insufficient data
    """
    if len(prices) < period:
        return None

    # EMA calculation with smoothing factor
    multiplier = 2.0 / (period + 1)
    ema = prices[0]  # Start with first price

    for price in prices[1:]:
        ema = (price * multiplier) + (ema * (1 - multiplier))

    return float(ema)


def calculate_atr(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 14
) -> Optional[float]:
    """
    Calculate Average True Range (volatility indicator)

    Args:
        highs: List of high prices
        lows: List of low prices
        closes: List of close prices
        period: Lookback period (default 14)

    Returns:
        ATR value or None if insufficient data
    """
    if len(highs) < period + 1 or len(lows) < period + 1 or len(closes) < period + 1:
        return None

    true_ranges = []
    for i in range(1, len(closes)):
        high = highs[i]
        low = lows[i]
        prev_close = closes[i - 1]

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        true_ranges.append(tr)

    # ATR is SMA of true ranges
    atr = np.mean(true_ranges[-period:])
    return float(atr)


def calculate_rsi(prices: List[float], period: int = 14) -> Optional[float]:
    """
    Calculate Relative Strength Index

    Args:
        prices: List of prices (most recent last)
        period: Lookback period (default 14)

    Returns:
        RSI value (0-100) or None if insufficient data
    """
    if len(prices) < period + 1:
        return None

    # Calculate price changes
    deltas = np.diff(prices)

    # Separate gains and losses
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)

    # Calculate average gain and loss
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])

    if avg_loss == 0:
        return 100.0  # No losses = maximum RSI

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))

    return float(rsi)


def calculate_bollinger_bands(
    prices: List[float],
    period: int = 20,
    num_std: float = 2.0
) -> Optional[Tuple[float, float, float]]:
    """
    Calculate Bollinger Bands

    Args:
        prices: List of prices (most recent last)
        period: Lookback period (default 20)
        num_std: Number of standard deviations (default 2.0)

    Returns:
        (upper, middle, lower) tuple or None if insufficient data
    """
    if len(prices) < period:
        return None

    recent_prices = prices[-period:]
    middle = np.mean(recent_prices)
    std = np.std(recent_prices)

    upper = middle + (num_std * std)
    lower = middle - (num_std * std)

    return float(upper), float(middle), float(lower)


def calculate_macd(
    prices: List[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9
) -> Optional[Tuple[float, float, float]]:
    """
    Calculate MACD (Moving Average Convergence Divergence)

    Args:
        prices: List of prices (most recent last)
        fast_period: Fast EMA period (default 12)
        slow_period: Slow EMA period (default 26)
        signal_period: Signal line EMA period (default 9)

    Returns:
        (macd, signal, histogram) tuple or None if insufficient data
    """
    if len(prices) < slow_period:
        return None

    # Calculate fast and slow EMAs
    fast_ema = calculate_ema(prices, fast_period)
    slow_ema = calculate_ema(prices, slow_period)

    if fast_ema is None or slow_ema is None:
        return None

    # MACD line
    macd_line = fast_ema - slow_ema

    # For signal line, we need historical MACD values
    # Simplified: calculate over available data
    macd_values = []
    for i in range(slow_period, len(prices) + 1):
        f_ema = calculate_ema(prices[:i], fast_period)
        s_ema = calculate_ema(prices[:i], slow_period)
        if f_ema and s_ema:
            macd_values.append(f_ema - s_ema)

    if len(macd_values) < signal_period:
        return float(macd_line), 0.0, float(macd_line)

    signal_line = calculate_ema(macd_values, signal_period)
    if signal_line is None:
        signal_line = 0.0

    histogram = macd_line - signal_line

    return float(macd_line), float(signal_line), float(histogram)


def calculate_stochastic(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 14,
    smooth_k: int = 3,
    smooth_d: int = 3
) -> Optional[Tuple[float, float]]:
    """
    Calculate Stochastic Oscillator (%K and %D)

    Args:
        highs: List of high prices
        lows: List of low prices
        closes: List of close prices
        period: Lookback period (default 14)
        smooth_k: %K smoothing period (default 3)
        smooth_d: %D smoothing period (default 3)

    Returns:
        (%K, %D) tuple or None if insufficient data
    """
    if len(highs) < period or len(lows) < period or len(closes) < period:
        return None

    # Calculate raw stochastic values
    stoch_values = []
    for i in range(period - 1, len(closes)):
        period_high = max(highs[i - period + 1:i + 1])
        period_low = min(lows[i - period + 1:i + 1])

        if period_high == period_low:
            stoch_values.append(50.0)  # Neutral
        else:
            stoch = ((closes[i] - period_low) / (period_high - period_low)) * 100
            stoch_values.append(stoch)

    if len(stoch_values) < smooth_k:
        return None

    # %K = smoothed stochastic
    k_value = np.mean(stoch_values[-smooth_k:])

    # %D = smoothed %K
    if len(stoch_values) < smooth_k + smooth_d - 1:
        d_value = k_value
    else:
        k_values = [np.mean(stoch_values[i:i + smooth_k])
                    for i in range(len(stoch_values) - smooth_k + 1)]
        d_value = np.mean(k_values[-smooth_d:])

    return float(k_value), float(d_value)


def calculate_momentum(prices: List[float], period: int = 10) -> Optional[float]:
    """
    Calculate price momentum (rate of change)

    Args:
        prices: List of prices (most recent last)
        period: Lookback period (default 10)

    Returns:
        Momentum as percentage change or None if insufficient data
    """
    if len(prices) < period + 1:
        return None

    current = prices[-1]
    past = prices[-period - 1]

    momentum = ((current - past) / past) * 100
    return float(momentum)


def calculate_vwap(
    prices: List[float],
    volumes: List[float]
) -> Optional[float]:
    """
    Calculate Volume Weighted Average Price

    Args:
        prices: List of prices (typically close prices)
        volumes: List of volumes (same length as prices)

    Returns:
        VWAP or None if insufficient data
    """
    if len(prices) != len(volumes) or len(prices) == 0:
        return None

    total_volume = sum(volumes)
    if total_volume == 0:
        return None

    vwap = sum(p * v for p, v in zip(prices, volumes)) / total_volume
    return float(vwap)


# ============================================================================
# Testing utility
# ============================================================================

def test_indicators():
    """Test indicator calculations"""
    print("\n=== Indicator Functions Test ===")

    # Mock price data (uptrend)
    prices = [100 + i * 0.5 for i in range(50)]
    highs = [p + 1 for p in prices]
    lows = [p - 1 for p in prices]
    volumes = [1000 + i * 10 for i in range(50)]

    print(f"\nPrice data: {len(prices)} bars")
    print(f"Latest price: ${prices[-1]:.2f}")

    # Test SMA
    sma_20 = calculate_sma(prices, 20)
    print(f"\nSMA(20): ${sma_20:.2f}")

    # Test EMA
    ema_20 = calculate_ema(prices, 20)
    print(f"EMA(20): ${ema_20:.2f}")

    # Test ATR
    atr = calculate_atr(highs, lows, prices, 14)
    print(f"ATR(14): ${atr:.2f}")

    # Test RSI
    rsi = calculate_rsi(prices, 14)
    print(f"RSI(14): {rsi:.2f}")

    # Test Bollinger Bands
    bb = calculate_bollinger_bands(prices, 20, 2.0)
    if bb:
        print(f"Bollinger Bands: Upper=${bb[0]:.2f}, Middle=${bb[1]:.2f}, Lower=${bb[2]:.2f}")

    # Test Momentum
    momentum = calculate_momentum(prices, 10)
    print(f"Momentum(10): {momentum:.2f}%")

    # Test VWAP
    vwap = calculate_vwap(prices, volumes)
    print(f"VWAP: ${vwap:.2f}")

    print("\n✓ All indicators calculated successfully")


if __name__ == "__main__":
    test_indicators()