"""Data model for price updates."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PriceUpdate:
    """A single price update for one ticker. The only type leaving the market layer."""

    ticker: str
    price: float
    previous_price: float
    timestamp: float  # Unix seconds
    change: float  # price - previous_price
    direction: str  # "up", "down" or "flat"

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previous_price": self.previous_price,
            "timestamp": self.timestamp,
            "change": self.change,
            "direction": self.direction,
        }
