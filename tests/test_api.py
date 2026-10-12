import pytest
from fastapi.testclient import TestClient

from quakerisk.server import create_app


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


def test_scenario_endpoint(client):
    response = client.post("/api/scenario", json={"lon": 6.5, "lat": 50.8, "mag": 6.0, "depth": 10, "strike": 135})
    assert response.status_code == 200
    body = response.json()
    assert body["loss_usd"] > 0 and body["seconds"] < 15
    assert len(body["layers"]["r6"]["ids"]) > 0 and len(body["scenario"]["rupture"]) == 4


@pytest.mark.parametrize("payload", [{"lon": 6.5, "lat": 50.8, "mag": 8.5}, {"lon": 40, "lat": 50.8, "mag": 6},
                                     {"lon": 6.5, "lat": 50.8, "mag": 3.0}])
def test_invalid_input_is_rejected(client, payload):
    assert client.post("/api/scenario", json=payload).status_code == 422


def test_index_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200 and "Germany Earthquake Risk" in response.text


def test_event_set_event_reproduces_its_table_loss(client):
    """A scenario with the exact parameters of a listed event gives the loss of the event loss table."""
    import pandas as pd

    from quakerisk.eventloss import elt_path

    path = elt_path("es20k", "ensemble")
    if not path.exists():
        pytest.skip("event losses not computed")
    e = pd.read_parquet(path).nlargest(1, "loss").iloc[0]
    body = {"lon": e.lon, "lat": e.lat, "mag": e.mag, "depth": max(e.depth, 3.0), "strike": e.strike, "rake": e.rake,
            "dip": e.dip, "gmpe": "ensemble"}
    assert client.post("/api/scenario", json=body).json()["loss_usd"] == pytest.approx(e.loss, rel=1e-6)
