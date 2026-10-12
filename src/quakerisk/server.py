"""Web backend: serves the dashboard (web/index.html) and runs scenarios.

Start with `quakerisk serve`, then open http://localhost:8000.
"""
import time

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .engine import QuakeEngine
from .paths import WEB

# fault mechanisms as (rake, dip)
MECHANISMS = {"normal": (-90.0, 60.0), "strike-slip": (0.0, 90.0), "thrust": (90.0, 40.0)}
GMPE_CHOICES = ("ensemble", "eshm20")


class Scenario(BaseModel):
    lon: float = Field(ge=5.0, le=16.0)
    lat: float = Field(ge=46.5, le=55.5)
    mag: float = Field(ge=4.0, le=8.0)
    depth: float = Field(default=10.0, ge=1.0, le=30.0)
    strike: float = Field(default=0.0, ge=0.0, le=360.0)
    mechanism: str = "normal"
    gmpe: str = "ensemble"
    rake: float | None = Field(default=None, ge=-180.0, le=180.0)  # exact mechanism of an event; overrides `mechanism`
    dip: float | None = Field(default=None, gt=0.0, le=90.0)


def create_app() -> FastAPI:
    app = FastAPI(title="Germany earthquake scenarios")
    app.add_middleware(GZipMiddleware, minimum_size=2000)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:8765", "http://127.0.0.1:8765"],
                       allow_methods=["POST"], allow_headers=["*"])
    engine = QuakeEngine()

    @app.post("/api/scenario")
    def scenario(s: Scenario):
        started = time.time()
        rake, dip = MECHANISMS.get(s.mechanism, MECHANISMS["normal"])
        if s.rake is not None and s.dip is not None:
            rake, dip = s.rake, s.dip
        result = engine.run(s.lon, s.lat, s.mag, s.depth, s.strike, dip, rake,
                            gmpe=s.gmpe if s.gmpe in GMPE_CHOICES else "ensemble")
        out = result.summary()
        out["layers"] = {"r6": result.hex_layers(6), "r7": result.hex_layers(7)}
        out["seconds"] = round(time.time() - started, 2)
        return out

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")

    return app


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    uvicorn.run(create_app(), host=host, port=port)
