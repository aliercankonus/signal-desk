"""Venue registry: which markets this system can collect from, and where their code lives.

Adding a market (for example a stock broker) means implementing the pieces listed in
`VenueSpec` for it, registering it here, and adding tests. The rest of the pipeline
(features, replay, research, paper fill model) works on the normalized market events
defined by `schemas/market_event.schema.json` and does not depend on the venue.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VenueSpec:
    name: str
    asset_class: str          # "crypto" or "stock"
    product_types: frozenset  # e.g. {"spot"}
    collector_module: str     # module exposing main() for --mode idle/once/stream/continuous
    trading_hours: str        # "24/7" or a session description; collectors must handle closed markets
    notes: str = ""


VENUES: dict[str, VenueSpec] = {
    "binance": VenueSpec(
        name="binance",
        asset_class="crypto",
        product_types=frozenset({"spot"}),
        collector_module="signal_desk.collector.binance_collector",
        trading_hours="24/7",
        notes="Public order book and trade streams; no credentials needed for market data.",
    ),
    # Planned (see docs/ROADMAP.md, Phase 5): a stock venue such as Alpaca or Interactive
    # Brokers paper trading. Not registered until it has a collector and tests.
}


class UnsupportedVenue(ValueError):
    pass


def resolve(venue_config: dict) -> VenueSpec:
    """Return the spec for a config's `venue` block, or raise UnsupportedVenue."""
    name = venue_config.get("name")
    spec = VENUES.get(name)
    if spec is None:
        raise UnsupportedVenue(f"Venue {name!r} is not implemented; registered: {sorted(VENUES)}")
    if venue_config.get("asset_class") != spec.asset_class:
        raise UnsupportedVenue(f"Venue {name!r} is {spec.asset_class}, config says {venue_config.get('asset_class')!r}")
    if venue_config.get("product_type") not in spec.product_types:
        raise UnsupportedVenue(f"Venue {name!r} supports {sorted(spec.product_types)}, not {venue_config.get('product_type')!r}")
    return spec
