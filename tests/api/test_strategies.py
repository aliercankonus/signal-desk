from datetime import date

from signal_desk.api import strategies as st
from signal_desk.research.klines_v3 import KlinesError


class FakeSession:
    """Serves monthly zips for months in `monthly`, daily zips always; 404 otherwise."""

    def __init__(self, monthly):
        self.monthly, self.urls = monthly, []


def fake_get(session, url):
    session.urls.append(url)
    name = url.rsplit("/", 1)[1]
    zipname = name.removesuffix(".CHECKSUM")
    body = b"zip:" + zipname.encode()
    if "/monthly/" in url and not any(m in zipname for m in session.monthly):
        raise KlinesError(f"HTTP 404 for {url}")
    import hashlib

    class R:
        content = body
        text = f"{hashlib.sha256(body).hexdigest()}  {zipname}\n"
    return R()


def test_sync_daily_then_monthly_replaces_days(monkeypatch, tmp_path):
    monkeypatch.setattr(st, "_get", fake_get)
    s = FakeSession(monthly=[])
    st.sync(s, "B", "klines/X/1h", "X-1h", tmp_path, "2026-10", date(2026, 11, 3))
    names = sorted(p.name for p in tmp_path.glob("*.zip"))
    assert "X-1h-2026-10-31.zip" in names and "X-1h-2026-11-02.zip" in names
    assert "X-1h-2026-11-03.zip" not in names and not any("/monthly/X-1h-2026-11.zip" in u for u in s.urls)
    st.sync(FakeSession(monthly=["2026-10"]), "B", "klines/X/1h", "X-1h", tmp_path, "2026-10", date(2026, 11, 3))
    names = sorted(p.name for p in tmp_path.glob("*.zip"))
    assert names == ["X-1h-2026-10.zip", "X-1h-2026-11-01.zip", "X-1h-2026-11-02.zip"]
    assert not list(tmp_path.glob("X-1h-2026-10-??.zip.CHECKSUM"))


def test_build_strategies_waiting_then_written(monkeypatch, tmp_path):
    config = {"storage": {"base_path": str(tmp_path)}}
    assert st.build_strategies(config) == {"status": "waiting for first refresh"}

    def boom(*a):
        raise RuntimeError("offline")
    monkeypatch.setattr(st, "download_all", boom)
    out = st.refresh(config, session=object(), today=date(2026, 11, 17))
    assert out["download_error"] == "offline"
    assert {v["status"] for v in out["strategies"].values()} == {"waiting"}
    assert st.build_strategies(config) == out
