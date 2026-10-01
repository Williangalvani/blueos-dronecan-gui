import asyncio
import logging
import os
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.bus import DEFAULT_URL, Bus, firmware_dir

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

STATIC = Path(__file__).resolve().parent.parent / "static"
bus = Bus()
app = FastAPI(title="DroneCAN GUI")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def revalidate(request, call_next):
    response = await call_next(request)
    # Image updates reuse the same tag, so the browser must not run a cached page against a new API.
    response.headers["Cache-Control"] = "no-cache"
    return response


class ConnectBody(BaseModel):
    url: str = DEFAULT_URL
    node_id: int = Field(default=127, ge=1, le=127)
    bus: int = Field(default=1, ge=1, le=4)
    target_system: int = Field(default=0, ge=0, le=255)
    signing_key: str = ""


class AllocatorBody(BaseModel):
    enabled: bool


class ParamBody(BaseModel):
    name: str
    type: str
    value: str | int | float | bool | None = None


class OpcodeBody(BaseModel):
    opcode: str


def _call(action):
    try:
        return action()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/register_service")
def register_service():
    return {
        "name": "DroneCAN GUI",
        "description": "DroneCAN node configuration and bus monitor through the autopilot MAVLink tunnel.",
        "icon": "mdi-lan",
        "company": "Willian Galvani",
        "version": "1.0.0",
        "webpage": "https://dronecan.github.io/GUI_Tool/Overview/",
        "api": "https://dronecan.github.io/",
        "works_in_relative_paths": True,
    }


@app.get("/api/status")
def status():
    return bus.snapshot()


@app.post("/api/connect")
def connect(body: ConnectBody):
    try:
        bus.connect(body.url, body.node_id, body.bus, body.target_system, body.signing_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return bus.snapshot()


@app.post("/api/disconnect")
def disconnect():
    bus.disconnect()
    return bus.snapshot()


@app.post("/api/allocator")
def allocator(body: AllocatorBody):
    return _call(lambda: bus.set_allocator(body.enabled) or bus.snapshot())


@app.post("/api/nodes/{node_id}/params/fetch")
def fetch_params(node_id: int):
    return _call(lambda: bus.fetch_params(node_id) or {"ok": True})


@app.post("/api/nodes/{node_id}/params")
def set_param(node_id: int, body: ParamBody):
    return _call(lambda: bus.set_param(node_id, body.name, body.type, body.value))


@app.post("/api/nodes/{node_id}/opcode")
def opcode(node_id: int, body: OpcodeBody):
    return _call(lambda: {"result": bus.opcode(node_id, body.opcode)})


@app.post("/api/nodes/{node_id}/restart")
def restart(node_id: int):
    return _call(lambda: {"result": bus.restart(node_id)})


@app.post("/api/nodes/{node_id}/firmware")
async def firmware(node_id: int, file: UploadFile = File(...)):
    name = Path(file.filename or "").name
    if not name or name.startswith("."):
        raise HTTPException(status_code=400, detail="firmware file name is required")
    dest = firmware_dir() / name
    try:
        with dest.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                output.write(chunk)
        _call(lambda: bus.begin_firmware(node_id, dest))
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"file": name}


@app.websocket("/api/ws")
async def websocket(socket: WebSocket):
    await socket.accept()
    loop = asyncio.get_running_loop()
    pending = asyncio.Queue(maxsize=1)

    def listener(snapshot):
        def push():
            if pending.full():
                try:
                    pending.get_nowait()
                except Exception:
                    pass
            pending.put_nowait(snapshot)

        loop.call_soon_threadsafe(push)

    bus.add_listener(listener)
    try:
        while True:
            await socket.send_json(await pending.get())
    except WebSocketDisconnect:
        pass
    finally:
        bus.remove_listener(listener)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


def main():
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()
