"""HTTP + WebSocket API around the exchange, and the web UI.

Every endpoint is ``async`` on purpose: they all run on the single asyncio event loop,
together with the simulation and the broadcaster. The engine is therefore only ever
used by one task at a time and needs no lock.

Run with ``python -m server`` and open http://127.0.0.1:8000 (API docs at /docs).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .exchange import Exchange, OrderNotFound
from .simulation import Simulation

STATIC = Path(__file__).parent / "static"
TraderName = Annotated[str, Field(min_length=1, max_length=24, pattern=r"^[\w.\- ]+$")]


class OrderIn(BaseModel):
    trader: TraderName
    side: Literal["buy", "sell"]
    type: Literal["limit", "market"] = "limit"
    price: Decimal | None = Field(default=None, gt=0)
    quantity: int = Field(gt=0, le=1_000_000)
    tif: Literal["gtc", "ioc", "fok"] | None = None
    post_only: bool = False


class ModifyIn(BaseModel):
    trader: TraderName
    price: Decimal | None = Field(default=None, gt=0)
    quantity: int | None = Field(default=None, gt=0, le=1_000_000)


class SimIn(BaseModel):
    running: bool | None = None
    speed: float | None = Field(default=None, gt=0, le=20)


def create_app(simulate: bool = True, seed: int | None = None) -> FastAPI:
    exchange = Exchange()
    sim = Simulation(exchange, seed=seed)
    sim.running = simulate
    clients: set[WebSocket] = set()

    async def simulation_loop() -> None:
        while True:
            if sim.running:
                sim.step()
            await asyncio.sleep(0.25 / sim.speed)

    async def broadcast_loop() -> None:
        sent = -1
        while True:
            await asyncio.sleep(0.1)
            if exchange.version == sent or not clients:
                continue
            sent = exchange.version
            message = json.dumps({**exchange.state(), "sim": sim_state()})
            for ws in list(clients):
                try:
                    await ws.send_text(message)
                except Exception:
                    clients.discard(ws)

    def sim_state() -> dict:
        return {"running": sim.running, "speed": sim.speed}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        tasks = [asyncio.create_task(simulation_loop()), asyncio.create_task(broadcast_loop())]
        yield
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(
        title="TFA Order Book",
        description="Price-time priority matching engine with a live trading UI.",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.exchange = exchange
    app.state.sim = sim

    def bad_request(error: Exception) -> HTTPException:
        return HTTPException(status_code=400, detail=str(error))

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/state", summary="Everything the UI shows, in one call")
    async def state(levels: int = Query(30, ge=1, le=200)):
        return {**exchange.state(levels), "sim": sim_state()}

    @app.get("/api/book", summary="Best bid/ask and the top N levels on each side")
    async def book(levels: int = Query(10, ge=0, le=200)):
        return exchange.book_view(levels)

    @app.post("/api/orders", summary="Submit a limit or market order")
    async def place(order: OrderIn):
        try:
            return exchange.place(
                order.trader,
                order.side,
                order.quantity,
                order.type,
                order.price,
                order.tif,
                order.post_only,
            )
        except (ValueError, TypeError) as error:
            raise bad_request(error) from error

    @app.get("/api/orders", summary="Open orders, optionally for one trader")
    async def orders(trader: str | None = None):
        return [exchange.order_dict(o) for o in exchange.book.open_orders(trader)]

    @app.patch("/api/orders/{order_id}", summary="Change price and/or quantity of an order")
    async def modify(order_id: int, change: ModifyIn):
        try:
            return exchange.modify(order_id, change.trader, change.price, change.quantity)
        except OrderNotFound as error:
            raise HTTPException(404, str(error)) from error
        except (ValueError, TypeError) as error:
            raise bad_request(error) from error

    @app.delete("/api/orders/{order_id}", summary="Cancel an order")
    async def cancel(order_id: int, trader: str):
        try:
            exchange.cancel(order_id, trader)
        except OrderNotFound as error:
            raise HTTPException(404, str(error)) from error
        return {"cancelled": order_id}

    @app.delete("/api/orders", summary="Cancel all the orders of a trader")
    async def cancel_all(trader: str):
        return {"cancelled": exchange.cancel_all(trader)}

    @app.get("/api/trades", summary="Most recent trades, newest first")
    async def trades(limit: int = Query(50, ge=1, le=100)):
        return list(exchange.trades)[:limit]

    @app.get("/api/accounts", summary="Positions and PnL of every trader")
    async def accounts():
        return exchange.accounts()

    @app.get("/api/journal", summary="Download the command journal (JSON lines)")
    async def journal():
        return PlainTextResponse(
            exchange.journal.to_jsonl(),
            headers={"Content-Disposition": "attachment; filename=journal.jsonl"},
        )

    @app.post("/api/sim", summary="Start/stop the simulated traders or change their speed")
    async def simulation(change: SimIn):
        if change.running is not None:
            sim.running = change.running
        if change.speed is not None:
            sim.speed = change.speed
        exchange.touch()
        return sim_state()

    @app.post("/api/reset", summary="Start a fresh market")
    async def reset():
        exchange.reset()
        sim.reset()
        return {"ok": True}

    @app.websocket("/ws")
    async def websocket(ws: WebSocket):
        await ws.accept()
        clients.add(ws)
        try:
            await ws.send_text(json.dumps({**exchange.state(), "sim": sim_state()}))
            while True:
                await ws.receive_text()  # the client only listens; this detects disconnects
        except WebSocketDisconnect:
            pass
        finally:
            clients.discard(ws)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
