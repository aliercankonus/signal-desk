import copy

import pytest
import yaml

from signal_desk.config.loader import ConfigError, _validate_operational_invariants
from signal_desk.venues import VENUES, UnsupportedVenue, resolve


def example():
    with open("config/config.example.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_example_config_resolves_to_binance_crypto():
    spec = resolve(example()["venue"])
    assert spec is VENUES["binance"]
    assert spec.asset_class == "crypto"


@pytest.mark.parametrize("change", [
    {"name": "alpaca", "asset_class": "stock"},
    {"asset_class": "stock"},
    {"product_type": "futures"},
])
def test_unregistered_or_mismatched_venues_are_rejected(change):
    venue = {**example()["venue"], **change}
    with pytest.raises(UnsupportedVenue):
        resolve(venue)


def test_config_invariants_reject_unimplemented_venue():
    cfg = copy.deepcopy(example())
    cfg["venue"].update(name="alpaca", asset_class="stock")
    with pytest.raises(ConfigError, match="not implemented"):
        _validate_operational_invariants(cfg)
