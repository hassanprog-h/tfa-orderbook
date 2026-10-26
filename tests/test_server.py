"""Smoke tests of the HTTP API and of the simulated market."""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from orderbook import fingerprint, replay  # noqa: E402
from server.main import create_app  # noqa: E402


@pytest.fixture
def client():
    with TestClient(create_app(simulate=False)) as client:
        yield client


def order(client, **kwargs):
    body = {"trader": "alice", "side": "buy", "quantity": 1, "price": "100.00", **kwargs}
    return client.post("/api/orders", json=body)


def test_place_and_match_with_decimal_prices(client):
    assert order(client, side="sell", price="100.05", quantity=5, trader="bob").status_code == 200
    response = order(client, price=100.10, quantity=3)
    body = response.json()
    assert body["status"] == "filled"
    assert body["trades"][0]["price"] == 100.05  # resting price
    assert client.get("/api/book").json()["best_ask"] == 100.05


def test_off_tick_price_is_a_400(client):
    response = order(client, price="100.001")
    assert response.status_code == 400
    assert "tick size" in response.json()["detail"]


def test_invalid_payload_is_a_422(client):
    assert order(client, quantity=0).status_code == 422
    assert order(client, side="hold").status_code == 422


def test_market_order(client):
    order(client, side="sell", price="101", quantity=2, trader="bob")
    body = order(client, type="market", price=None, quantity=5).json()
    assert body["filled"] == 2 and body["status"] == "cancelled"


def test_cancel_only_own_orders(client):
    order_id = order(client).json()["order_id"]
    assert client.delete(f"/api/orders/{order_id}", params={"trader": "mallory"}).status_code == 404
    assert client.delete(f"/api/orders/{order_id}", params={"trader": "alice"}).status_code == 200
    assert client.get("/api/orders").json() == []


def test_modify(client):
    order_id = order(client, quantity=10).json()["order_id"]
    response = client.patch(f"/api/orders/{order_id}", json={"trader": "alice", "quantity": 4})
    assert response.json()["resting"] == 4


def test_accounts_and_pnl(client):
    order(client, side="sell", price="100", quantity=10, trader="bob")
    order(client, price="100", quantity=10)
    order(client, side="sell", price="101", quantity=1, trader="carol")
    order(client, price="101", quantity=1, trader="dave")  # mark moves to 101
    accounts = {a["trader"]: a for a in client.get("/api/accounts").json()}
    assert accounts["alice"]["position"] == 10
    assert accounts["alice"]["pnl"] == 10.0
    assert accounts["bob"]["pnl"] == -10.0


def test_state_and_journal(client):
    order(client, side="sell", price="100", trader="bob")
    order(client, price="100")
    state = client.get("/api/state").json()
    assert state["last"] == 100.0 and state["volume"] == 1
    assert len(state["candles"]) == 1

    lines = client.get("/api/journal").text.splitlines()
    assert len(lines) == 3  # config + 2 orders


def test_simulation_produces_a_consistent_market():
    app = create_app(simulate=False, seed=7)
    sim, exchange = app.state.sim, app.state.exchange
    for _ in range(300):
        sim.step()
    book = exchange.book
    assert exchange.trade_count > 50
    assert book.best_bid() < book.best_ask()
    # The whole simulated session can be rebuilt from its journal.
    assert fingerprint(replay(exchange.journal.entries)) == fingerprint(book)


def test_websocket_pushes_state(client):
    with client.websocket_connect("/ws") as ws:
        state = ws.receive_json()
        assert state["symbol"] == "TFA" and "sim" in state
