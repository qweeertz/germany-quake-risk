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
