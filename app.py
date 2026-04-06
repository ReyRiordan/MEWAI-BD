import os
import re
import time
import json
import asyncio
import threading
import tempfile
import base64
from dataclasses import dataclass, field

import numpy as np
import soundfile as sf
import requests
import gradio as gr
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastrtc import ReplyOnPause, Stream, AlgoOptions
from numpy.typing import NDArray


load_dotenv('.env')
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
FIREWORKS_API_KEY = os.getenv("FIREWORKS_API_KEY")
INWORLD_API_KEY = os.getenv("INWORLD_API_KEY")


# --- STT ---

class WhisperSTT:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.url = "https://audio-prod.api.fireworks.ai/v1/audio/transcriptions"

    def transcribe(self, audio: tuple[int, NDArray[np.int16 | np.float32]]) -> str:
        sr, arr = audio
        if arr.ndim > 1:
            arr = np.squeeze(arr, axis=0)
        if arr.dtype != np.int16:
            arr = np.clip(arr, -1.0, 1.0)
            arr = (arr * 32767.0).astype(np.int16)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            temp_path = f.name
            sf.write(temp_path, arr, sr, subtype="PCM_16")
        with open(temp_path, "rb") as audio_file:
            response = requests.post(
                self.url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": audio_file},
                data={"model": "whisper-v3", "temperature": "0", "vad_model": "silero"},
            )
        if response.status_code == 200:
            return response.json()['text']
        else:
            raise Exception(f"Transcription failed: {response.status_code} - {response.text}")


# --- LLM ---

class OpenRouterChat:
    def __init__(self):
        self.api_key = OPENROUTER_API_KEY
        self.url = "https://openrouter.ai/api/v1/chat/completions"

    def chat(self, messages: list[dict], system_prompt: str) -> str:
        payload = {
            "model": "anthropic/claude-haiku-4.5",
            "reasoning": {"enabled": False},
            "messages": [],
        }
        if system_prompt:
            payload["messages"].append({"role": "system", "content": system_prompt})
        payload["messages"].extend(messages)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        resp = requests.post(self.url, json=payload, headers=headers, timeout=120)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


# --- TTS ---

class InworldTTS:
    def __init__(self):
        self.api_key = INWORLD_API_KEY
        self.url = "https://api.inworld.ai/tts/v1/voice:stream"

    def stream_tts_sync(self, response_text: str, options: dict):
        payload = {
            "text": response_text,
            "voiceId": options['voice'],
            "modelId": "inworld-tts-1.5-mini",
            "audio_config": {
                "audio_encoding": "LINEAR16",
                "sample_rate_hertz": 48000,
                "speakingRate": options['speed'],
            },
        }
        headers = {
            "Authorization": f"Basic {self.api_key}",
            "Content-Type": "application/json",
        }
        response = requests.post(self.url, json=payload, headers=headers, stream=True)
        response.raise_for_status()
        sample_rate = payload["audio_config"]["sample_rate_hertz"]
        for line in response.iter_lines():
            if not line:
                continue
            try:
                if isinstance(line, bytes):
                    line = line.decode('utf-8')
                chunk = json.loads(line)
                audio_chunk = base64.b64decode(chunk["result"]["audioContent"])
                if len(audio_chunk) > 44:
                    pcm = audio_chunk[44:]
                    waveform = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
                    yield (sample_rate, waveform)
            except json.JSONDecodeError as e:
                print(f"JSON decode error: {e}, Line: {line}")
                continue
            except Exception as e:
                print(f"Error processing chunk: {e}, Line: {line}")
                continue


# --- Patient prompt builder ---

def build_patient_prompt(base: str, case: dict) -> str:
    base = base.replace("{patient_name}", case['demographics']['name'])
    parts = ["\n\n=== PATIENT CASE DETAILS ===\n"]
    demo = case['demographics']
    parts.append(
        f"<demographics>\n"
        f"name: {demo['name']}\n"
        f"date_of_birth: {demo['date_of_birth']}\n"
        f"sex: {demo['sex']}\n"
        f"gender: {demo['gender']}\n"
        f"background: {demo['background']}\n"
        f"</demographics>\n"
    )
    if "behavior" in case:
        parts.append(f"<behavior>\n{case['behavior']}\n</behavior>\n")
    parts.append(f"<chief_concern>\n{case['chief_concern']}\n</chief_concern>\n")
    free_items = "\n".join(f"- {item}" for item in case['free_information'])
    parts.append(
        f"<free_information>\n"
        f"Information you may volunteer or mention naturally:\n"
        f"{free_items}\n"
        f"</free_information>\n"
    )
    locked_items = "\n".join(f"- {item}" for item in case['locked_information'])
    parts.append(
        f"<locked_information>\n"
        f"Information to ONLY reveal when the student asks appropriate, specific questions:\n"
        f"{locked_items}\n"
        f"</locked_information>"
    )
    return base + "\n".join(parts)


# --- GameState ---

@dataclass
class GameState:
    status: str = "idle"  # idle | active | success | fail
    escalation: int = 5
    current_scene: str = "background.jpg"
    actions_taken: list = field(default_factory=list)
    timer_start: float | None = None
    timer_elapsed: int = 0


# --- Module-level singletons ---

GAME_STATE = GameState()
STATE_LOCK = threading.Lock()
WS_CLIENTS: set[WebSocket] = set()
EVENT_QUEUE: asyncio.Queue = None
MAIN_LOOP = None
CONVERSATION_HISTORY: list[dict] = []
HISTORY_LOCK = threading.Lock()

SCENARIO = json.load(open("resources/scenario_1.json"))
PATIENT = json.load(open("resources/patient.json"))
PATIENT_PROMPT = build_patient_prompt(open("resources/patient.txt", encoding="utf8").read(), PATIENT)
SYSTEM_PROMPT = open("resources/system.txt", encoding="utf8").read()

STT = WhisperSTT(FIREWORKS_API_KEY)
LLM = OpenRouterChat()
TTS = InworldTTS()


# --- Helper functions ---

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


def run_system_agent(text: str, escalation: int) -> list[dict]:
    user_msg = json.dumps({
        "utterance": text,
        "escalation": escalation,
        "actions": SCENARIO["actions"],
    })
    raw = LLM.chat([{"role": "user", "content": user_msg}], SYSTEM_PROMPT)
    clean = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    try:
        return json.loads(clean).get("detected_actions", [])
    except json.JSONDecodeError:
        print(f"[system_agent] JSON parse error. Raw: {raw!r}")
        return []


def apply_actions(detected: list[dict]):
    max_esc = SCENARIO["point_bar"]["max"]
    for action in detected:
        with STATE_LOCK:
            GAME_STATE.escalation = max(0, min(max_esc, GAME_STATE.escalation + action["point_change"]))
            if action.get("scene_change"):
                GAME_STATE.current_scene = action["scene_change"]
            GAME_STATE.actions_taken.append(action["type"])
        desc = next((a["desc"] for a in SCENARIO["actions"] if a["type"] == action["type"]), "")
        enqueue({
            "type": "action_detected",
            "action_type": action["type"],
            "desc": desc,
            "point_change": action["point_change"],
        })
        enqueue({
            "type": "state_update",
            "escalation": GAME_STATE.escalation,
            "max": max_esc,
            "scene": GAME_STATE.current_scene,
            "status": GAME_STATE.status,
        })


def check_terminal() -> bool:
    goal = SCENARIO["point_bar"]["goal"]
    max_esc = SCENARIO["point_bar"]["max"]
    with STATE_LOCK:
        esc = GAME_STATE.escalation
        if esc <= goal:
            GAME_STATE.status = "success"
            GAME_STATE.current_scene = SCENARIO["success"]
            enqueue({"type": "game_over", "status": "success", "reason": "Escalation reduced to goal"})
            return True
        if esc >= max_esc:
            GAME_STATE.status = "fail"
            GAME_STATE.current_scene = SCENARIO["fail"]
            enqueue({"type": "game_over", "status": "fail", "reason": "Escalation reached maximum"})
            return True
    return False


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


# --- FastRTC response handler ---

def response(audio: tuple[int, NDArray[np.int16 | np.float32]], session_id: str | None, chatbot=None):
    if GAME_STATE.status != "active":
        return

    text = STT.transcribe(audio)
    if not text.strip():
        return

    print(f"[student] {text}")
    enqueue({"type": "transcript_update", "role": "student", "content": text})

    with HISTORY_LOCK:
        CONVERSATION_HISTORY.append({"role": "user", "content": text})

    # System Agent: detect actions
    detected = run_system_agent(text, GAME_STATE.escalation)
    if detected:
        apply_actions(detected)

    if check_terminal():
        return

    # Patient Agent: generate reply
    max_esc = SCENARIO["point_bar"]["max"]
    escalation_ctx = f"[CURRENT ESCALATION: {GAME_STATE.escalation}/{max_esc}]"
    with HISTORY_LOCK:
        history_snapshot = list(CONVERSATION_HISTORY)
    messages = [{"role": "system", "content": escalation_ctx}] + history_snapshot
    reply = LLM.chat(messages, PATIENT_PROMPT)
    print(f"[patient] {reply}")

    with HISTORY_LOCK:
        CONVERSATION_HISTORY.append({"role": "assistant", "content": reply})

    enqueue({"type": "transcript_update", "role": "patient", "content": reply})

    for chunk in TTS.stream_tts_sync(reply, SCENARIO["speech"]):
        yield chunk


# --- FastRTC stream ---

algo_options = AlgoOptions(
    audio_chunk_duration=1.0,
    started_talking_threshold=0.3,
    speech_threshold=0.3,
)
stream = Stream(
    modality="audio",
    mode="send-receive",
    handler=ReplyOnPause(response, input_sample_rate=16000, algo_options=algo_options),
    concurrency_limit=1,
    ui_args={"title": "MEWAI Patient"},
)


# --- FastAPI app ---

app = FastAPI()


@app.on_event("startup")
async def startup():
    global EVENT_QUEUE, MAIN_LOOP
    EVENT_QUEUE = asyncio.Queue()
    MAIN_LOOP = asyncio.get_running_loop()
    asyncio.create_task(broadcast_events())


@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.get("/scenario")
async def scenario():
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
        # Send initial state on connect
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


app.mount("/scenes", StaticFiles(directory="scenes"), name="scenes")
app = gr.mount_gradio_app(app, stream.ui, path="/gradio")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
