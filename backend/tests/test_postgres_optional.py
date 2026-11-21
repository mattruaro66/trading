"""Executado somente se ATLAS_TEST_POSTGRES_URL estiver definido (ex.: no Docker Compose)."""
import os
from datetime import date

import pytest

from atlas.market_data.importers import ImportResult
from atlas.market_data.synthetic import generate_bars
from atlas.market_data.validation import ValidationReport
from atlas.persistence.models import Base
from atlas.persistence.repository import Repository

URL = os.environ.get("ATLAS_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="ATLAS_TEST_POSTGRES_URL não definido")


def test_postgres_roundtrip():
    r = Repository(URL)
    Base.metadata.drop_all(r.engine)
    r.create_all()
    bars = generate_bars("WDO", "WDOJ26_SINTETICO", date(2026, 1, 2), 2)
    res = ImportResult(bars, ValidationReport("WDO", "WDOJ26_SINTETICO"), "synthetic", [], "x", "SYN", True)
    assert r.save_import(res, "syn")["inserted"] == len(bars)
    assert r.load_bars("WDO", "WDOJ26_SINTETICO") == bars
