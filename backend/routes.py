import time
import asyncio

from fastapi import WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from backend import handlers
from backend.game import (
    GAME_STATE, STATE_LOCK, CONVERSATION_HISTORY, HISTORY_LOCK,
)


WS_CLIENTS: set[WebSocket] = set()
EVENT_QUEUE: asyncio.Queue = None
MAIN_LOOP = None

# Injected at startup by app.py
SCENARIO = None


def enqueue(event: dict):
    """Thread-safe bridge from sync FastRTC thread to async event queue."""
    if MAIN_LOOP is None or EVENT_QUEUE is None:
        print(f"[enqueue] dropped event before startup: {event}")
        return
    asyncio.run_coroutine_threadsafe(EVENT_QUEUE.put(event), MAIN_LOOP)


async def broadcast_events():
    while True:
        try:
            event = await EVENT_QUEUE.get()
        except asyncio.CancelledError:
            return
        dead = set()
        for ws in list(WS_CLIENTS):
            try:
                await ws.send_json(event)
            except Exception:
                dead.add(ws)
        WS_CLIENTS.difference_update(dead)


async def run_timer():
    limit = SCENARIO.get("time_limit", 300)
    while GAME_STATE.status == "active":
        await asyncio.sleep(1)
        with STATE_LOCK:
            if GAME_STATE.timer_start is None:
                continue
            elapsed = int(time.time() - GAME_STATE.timer_start)
            GAME_STATE.timer_elapsed = elapsed
        await EVENT_QUEUE.put({"type": "timer", "elapsed": elapsed, "limit": limit})
        if elapsed >= limit:
            with STATE_LOCK:
                GAME_STATE.status = "fail"
                GAME_STATE.current_scene = SCENARIO["fail"]
            await EVENT_QUEUE.put({
                "type": "game_over",
                "status": "fail",
                "reason": "Time limit reached",
            })
            return


def reset_game():
    with STATE_LOCK:
        GAME_STATE.status = "idle"
        GAME_STATE.escalation = SCENARIO["point_bar"]["start"]
        GAME_STATE.current_scene = SCENARIO["background"]
        GAME_STATE.actions_taken.clear()
        GAME_STATE.timer_start = None
        GAME_STATE.timer_elapsed = 0
    with HISTORY_LOCK:
        CONVERSATION_HISTORY.clear()


def register_routes(app, scenario: dict):
    global SCENARIO
    SCENARIO = scenario
    handlers.enqueue = enqueue

    @app.on_event("startup")
    async def startup():
        global EVENT_QUEUE, MAIN_LOOP
        EVENT_QUEUE = asyncio.Queue()
        MAIN_LOOP = asyncio.get_running_loop()
        asyncio.create_task(broadcast_events())

    @app.get("/")
    async def index():
        return FileResponse("frontend/index.html")

    @app.get("/scenario")
    async def get_scenario():
        return {
            "intro": SCENARIO["intro"],
            "goal": SCENARIO["goal"],
            "actions": SCENARIO["actions"],
            "point_bar": SCENARIO["point_bar"],
            "time_limit": SCENARIO.get("time_limit", 300),
        }

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket):
        await websocket.accept()
        WS_CLIENTS.add(websocket)
        try:
            await websocket.send_json({
                "type": "state_update",
                "escalation": GAME_STATE.escalation,
                "max": SCENARIO["point_bar"]["max"],
                "scene": GAME_STATE.current_scene,
                "status": GAME_STATE.status,
            })
            while True:
                data = await websocket.receive_json()
                msg_type = data.get("type")
                if msg_type == "begin":
                    if GAME_STATE.status != "active":
                        with STATE_LOCK:
                            GAME_STATE.status = "active"
                            GAME_STATE.timer_start = time.time()
                            GAME_STATE.current_scene = SCENARIO["start"]
                        await EVENT_QUEUE.put({
                            "type": "state_update",
                            "escalation": GAME_STATE.escalation,
                            "max": SCENARIO["point_bar"]["max"],
                            "scene": GAME_STATE.current_scene,
                            "status": GAME_STATE.status,
                        })
                        asyncio.create_task(run_timer())
                elif msg_type == "reset":
                    reset_game()
                    await EVENT_QUEUE.put({
                        "type": "state_update",
                        "escalation": GAME_STATE.escalation,
                        "max": SCENARIO["point_bar"]["max"],
                        "scene": GAME_STATE.current_scene,
                        "status": GAME_STATE.status,
                    })
        except WebSocketDisconnect:
            WS_CLIENTS.discard(websocket)
        except Exception as e:
            print(f"[ws] error: {e}")
            WS_CLIENTS.discard(websocket)
