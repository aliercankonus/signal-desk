"""Trades-only research: downloader checksum handling and runner rules (offline, synthetic)."""
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from signal_desk.research import binance_trades_download as dl
from signal_desk.research import trades_only as tr

SPEC_PATH = Path(__file__).resolve().parents[2] / "docs/research/trades_only_v1.json"
SYM = "BTCUSDT"
D0 = date(2026, 1, 1)


def _ts(day, sec=0):
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()) + sec


def write_day(out, day, rows, micro=True, header=False):
    """rows: (second_of_day, price, qty, buyer_is_maker)."""
    name = dl.zip_name(SYM, day)
    lines = ["id,price,qty,quote_qty,time,is_buyer_maker,is_best_match"] if header else []
    for i, (sec, price, qty, maker) in enumerate(rows):
        t = _ts(day) * 1000 + int(round(sec * 1000))
        lines.append(f"{i},{price},{qty},{price * qty},{t * 1000 if micro else t},{maker},True")
    path = out / name
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(name.replace(".zip", ".csv"), "\n".join(lines) + "\n")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    (out / f"{name}.CHECKSUM").write_text(f"{digest}  {name}\n")
    return path


def small_spec():
    spec = json.loads(SPEC_PATH.read_text())
    spec["window_utc"].update(start="2026-01-01T00:00:00Z", validation_start="2026-01-02T00:00:00Z",
                              test_start="2026-01-03T00:00:00Z", end="2026-01-04T00:00:00Z")
    return spec


def make_window(tmp_path, price_fn, maker=False, step=5):
    data = tmp_path / "data"
    data.mkdir()
    for k in range(3):
        day = date(2026, 1, 1 + k)
        write_day(data, day, [(s, price_fn(k * 86400 + s), 0.01, maker) for s in range(0, 86400, step)])
    return data


# --- spec ---------------------------------------------------------------

def test_spec_is_pinned_and_predeclared():
    spec, sha = tr.load_spec(SPEC_PATH)
    assert sha == tr.PINNED_SPEC_SHA256
    assert spec["review_status"] == "predeclared_not_executed"
    assert len(list(tr.configurations(spec))) == 12
    assert spec["chronological_split"]["purge_seconds_before_each_boundary"] >= max(spec["horizons_seconds"]) \
        + spec["execution_and_costs"]["execution_delay_seconds"]


def test_changed_spec_is_rejected(tmp_path):
    changed = tmp_path / "spec.json"
    changed.write_text(SPEC_PATH.read_text().replace('"fee_per_side": 0.001', '"fee_per_side": 0.0005'))
    with pytest.raises(tr.TradesOnlyError, match="pinned"):
        tr.load_spec(changed)


# --- downloader ---------------------------------------------------------

class FakeResponse:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    @property
    def text(self):
        return self.body.decode()

    def iter_content(self, size):
        for i in range(0, len(self.body), size):
            yield self.body[i:i + size]


class FakeSession:
    def __init__(self, files):
        self.files, self.calls = files, []

    def get(self, url, stream=False, timeout=None):
        self.calls.append(url)
        return FakeResponse(self.files[url]) if url in self.files else FakeResponse(b"", 404)


def published(day, payload=b"zip-bytes", checksum_payload=None):
    url = dl.zip_url(SYM, day)
    digest = hashlib.sha256(checksum_payload or payload).hexdigest()
    return {url: payload, url + ".CHECKSUM": f"{digest}  {dl.zip_name(SYM, day)}\n".encode()}


def test_download_verifies_and_keeps_checksum(tmp_path):
    session = FakeSession(published(D0))
    path = dl.download_day(session, tmp_path, SYM, D0)
    assert path.read_bytes() == b"zip-bytes"
    assert dl.verify_local(tmp_path, SYM, D0) == path
    assert not list(tmp_path.glob("*.part"))


def test_download_rejects_checksum_mismatch(tmp_path):
    session = FakeSession(published(D0, checksum_payload=b"other"))
    with pytest.raises(dl.DownloadError, match="mismatch"):
        dl.download_day(session, tmp_path, SYM, D0)
    assert list(tmp_path.iterdir()) == []


def test_download_skips_already_verified_file(tmp_path):
    files = published(D0)
    dl.download_day(FakeSession(files), tmp_path, SYM, D0)
    session = FakeSession(files)
    dl.download_day(session, tmp_path, SYM, D0)
    assert session.calls == [dl.zip_url(SYM, D0) + ".CHECKSUM"]


def test_checksum_must_name_the_file():
    with pytest.raises(dl.DownloadError, match="expected"):
        dl.parse_checksum("a" * 64 + "  ETHUSDT-trades-2026-01-01.zip", dl.zip_name(SYM, D0))
    with pytest.raises(dl.DownloadError, match="unparseable"):
        dl.parse_checksum("<html>404</html>", dl.zip_name(SYM, D0))


def test_http_error_and_tampered_local_file(tmp_path):
    with pytest.raises(dl.DownloadError, match="404"):
        dl.download_day(FakeSession({}), tmp_path, SYM, D0)
    write_day(tmp_path, D0, [(1, 100.0, 1.0, False)])
    (tmp_path / dl.zip_name(SYM, D0)).write_bytes(b"tampered")
    with pytest.raises(dl.DownloadError, match="mismatch"):
        dl.verify_local(tmp_path, SYM, D0)


# --- parsing and causal state -------------------------------------------

def test_parses_ms_and_us_timestamps_and_header(tmp_path):
    us = list(tr.iter_trades(write_day(tmp_path, D0, [(1.5, 100.0, 2.0, True)], micro=True, header=True)))
    (tmp_path / "ms").mkdir()
    ms = list(tr.iter_trades(write_day(tmp_path / "ms", D0, [(1.5, 100.0, 2.0, True)], micro=False)))
    assert us == ms == [(_ts(D0, 1) * 1_000_000 + 500_000, 100.0, 2.0, True)]


def test_buckets_include_trade_at_t_and_flow_window_is_half_open():
    secs = tr.Seconds(0, 200)
    secs.add([(30_000_000, 100.0, 1.0, False),   # exactly t=30: in (0,30], not in (30,60]
              (45_000_000, 101.0, 3.0, True),
              (60_000_000, 102.0, 1.0, False)])
    secs.finish()
    assert secs.price[30] == 100.0 and secs.price[59] == 101.0 and secs.price[60] == 102.0
    assert tr.score(secs, "signed_aggressor_qty_ratio_30s", 60) == pytest.approx((1 - 3) / 4)
    assert tr.score(secs, "signed_aggressor_qty_ratio_30s", 30) is None  # only 1 trade
    assert tr.score(secs, "momentum_60s", 90) == pytest.approx(102.0 / 100.0 - 1)
    assert secs.age(70) == 10 and secs.age(10) == float("inf")


def test_decreasing_time_and_missing_day_fail_closed(tmp_path):
    secs = tr.Seconds(0, 100)
    with pytest.raises(tr.TradesOnlyError, match="decreased"):
        secs.add([(5_000_000, 1.0, 1.0, False), (4_000_000, 1.0, 1.0, False)])
    write_day(tmp_path, D0, [(1, 100.0, 1.0, False)])
    with pytest.raises(dl.DownloadError, match="missing"):
        tr.load_range(tmp_path, SYM, _ts(D0), _ts(D0) + 2 * 86400, 10)


def test_costs_flat_price_loses_round_trip():
    spec = small_spec()
    gross, net = tr.net_return(spec, 100.0, 100.0)
    assert gross == 0
    assert net == pytest.approx((1 - 2e-4) * 0.999 / ((1 + 2e-4) * 1.001) - 1)
    assert -0.0025 < net < -0.0023


def test_purge_and_embargo_bounds(tmp_path):
    spec = small_spec()
    secs = tr.Seconds(_ts(D0), _ts(D0) + 3 * 86400)
    first, limit = tr.eligible_bounds(spec, secs, _ts(D0), _ts(D0) + 86400)
    assert first == 60 and limit == 86400 - 360          # warmup at window start; purge at boundary
    first, limit = tr.eligible_bounds(spec, secs, _ts(D0) + 86400, _ts(D0) + 2 * 86400)
    assert first == 86400 + 360 and limit == 2 * 86400 - 360  # embargo after boundary


# --- end to end ---------------------------------------------------------

def test_flat_market_validation_then_single_test_run_fails_after_costs(tmp_path):
    data = make_window(tmp_path, lambda s: 100.0)   # all buyer-aggressor, price never moves
    spec, sha = small_spec(), "x"
    val = tr.run_validation(spec, sha, data)
    assert val["phase"] == "validation" and len(val["configurations"]) == 12
    flow = [r for r in val["configurations"] if r["config"]["candidate"] == "flow_30s"]
    assert all(r["validation"]["trades"] >= 300 for r in flow if r["config"]["horizon_seconds"] == 60)
    assert all(r["validation"]["mean_net_return"] < 0 for r in flow)
    for r in flow:  # non-overlap, purge and embargo cap the count
        h = r["config"]["horizon_seconds"]
        assert r["validation"]["trades"] <= (86400 - 720) // (h + 30) + 1
    assert all(r["validation"]["trades"] == 0 for r in val["configurations"] if r["config"]["candidate"] == "momentum_60s")
    assert val["baselines"]["validation"]["no_change"]["trades"] == 0
    assert val["selected"]["candidate"] == "flow_30s"

    test = tr.run_test(spec, sha, data, val)
    assert test["verdict"] == "fail" and not test["checks"]["mean_net_positive"]
    assert test["test"]["trades"] >= 300
    with pytest.raises(tr.TradesOnlyError, match="spent"):
        tr.run_test(spec, sha, data, val)


def test_strong_uptrend_passes_profit_checks_but_must_beat_always_long(tmp_path):
    data = make_window(tmp_path, lambda s: 100.0 * (1 + 1e-4) ** s)  # ~+0.6% per minute
    spec = small_spec()
    val = tr.run_validation(spec, "x", data)
    test = tr.run_test(spec, "x", data, val)
    assert test["checks"]["mean_net_positive"] and test["checks"]["total_net_positive"]
    # The flow rule fires on every slot here, so it cannot beat always-long: fail.
    assert not test["checks"]["beats_always_long_same_horizon"]
    assert test["verdict"] == "fail"


def test_test_phase_refuses_empty_or_foreign_selection(tmp_path):
    spec = small_spec()
    with pytest.raises(tr.TradesOnlyError, match="selected nothing"):
        tr.run_test(spec, "x", tmp_path, {"phase": "validation", "spec_sha256": "x", "selected": None})
    with pytest.raises(tr.TradesOnlyError, match="not a validation result"):
        tr.run_test(spec, "x", tmp_path, {"phase": "validation", "spec_sha256": "y", "selected": {}})
    assert not (tmp_path / tr.TEST_MARKER).exists()


def test_cli_refuses_existing_output(tmp_path):
    out = tmp_path / "out.json"
    out.write_text("{}")
    with pytest.raises(tr.TradesOnlyError, match="exists"):
        tr.main(["--spec", str(SPEC_PATH), "--data-dir", str(tmp_path), "--phase", "validation",
                 "--output", str(out)])
