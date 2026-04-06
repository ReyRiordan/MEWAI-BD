# MEWAI-BD POC Implementation Plan

## Context
Building a working local prototype of the behavioral de-escalation simulation for medical students. Students speak to an AI patient via real-time voice. A system agent detects their de-escalation actions from speech, updates an escalation bar, changes scene images. Goal: reduce escalation from 5→0 before hitting max (10) or the time limit.

Current state: working FastRTC voice-to-voice demo (`simulation.py`) + all scene JPGs + `scenario_1.json` (fixed) + all prompt/resource files + `requirements.txt`. Missing: game UI (`static/index.html`) and backend (`app.py`).

---

## Architecture

```
FastAPI (app.py)
├── GET /              → static/index.html
├── GET /scenes/*      → scenes/ images
├── GET /scenario      → scenario JSON (intro, goal, actions)
├── WebSocket /ws      → real-time game state events to browser
└── /gradio            → FastRTC + Gradio (WebRTC audio only)

Browser (static/index.html)
├── 4 screens: Start → Intro → Game → End
├── WebSocket /ws      → listens for state/action/timer/game_over events
└── Gradio iframe      → injected on "Begin" click, handles mic/speaker

Per student turn (FastRTC handler, sync thread):
  STT → System Agent (JSON: detected actions) → update GameState + WS push
  → check win/loss → Patient Agent → TTS stream back
```

**Global state (single-session POC):**  
`GameState` dataclass + `threading.Lock`. Async queue bridges sync FastRTC thread → async WS broadcast.

---

## Files to Create/Modify

| File | Action | Notes |
|------|--------|-------|
| `app.py` | ✅ Done | Main entry; all backend logic |
| `static/index.html` | Create | Single-file frontend (inline CSS/JS) |
| `resources/system.txt` | ✅ Done | System agent prompt (JSON-only, 4 few-shot examples) |
| `resources/patient.txt` | ✅ Done | Patient agent base prompt (escalation table, locked info gate) |
| `resources/patient.json` | ✅ Done | Patient case (Jordan, 22yo, ASD) |
| `resources/scenario_1.json` | ✅ Done | Fixed `"end.jpg"→"success.jpg"`, added `"time_limit": 300`, added `"speed": 1.0` to speech block |
| `requirements.txt` | ✅ Done | All Python deps |

`simulation.py` stays untouched as reference.

---

## Implementation Order

1. ~~**`resources/scenario_1.json`** — fix filename + add time_limit~~ ✅ DONE  
2. ~~**`resources/patient.json`** — patient case (format matches `build_patient_prompt()` in simulation.py)~~ ✅ DONE  
3. ~~**`resources/patient.txt`** — patient agent base prompt~~ ✅ DONE  
4. ~~**`resources/system.txt`** — system agent prompt (highest risk; needs few-shot examples)~~ ✅ DONE  
5. ~~**`requirements.txt`**~~ ✅ DONE  
6. ~~**`app.py`** — full backend~~ ✅ DONE  
7. **`static/index.html`** — frontend ← **next**  

---

## app.py Structure

### Key singletons (module-level)
```python
GAME_STATE = GameState()   # dataclass
STATE_LOCK = threading.Lock()
WS_CLIENTS: set[WebSocket] = set()
EVENT_QUEUE: asyncio.Queue = None      # set at startup
MAIN_LOOP = None                        # captured at startup
CONVERSATION_HISTORY: list[dict] = []
HISTORY_LOCK = threading.Lock()

SCENARIO = json.load(open("resources/scenario_1.json"))
PATIENT_PROMPT = build_patient_prompt(...)   # reuse from simulation.py
SYSTEM_PROMPT = open("resources/system.txt").read()
STT = WhisperSTT(...)   # copy class from simulation.py
LLM = OpenRouterChat()  # copy class from simulation.py  
TTS = InworldTTS()      # copy class from simulation.py
```

### GameState dataclass
```python
@dataclass
class GameState:
    status: str = "idle"   # idle | intro | active | success | fail
    escalation: int = 5
    current_scene: str = "background.jpg"
    actions_taken: list = field(default_factory=list)
    timer_start: float | None = None
    timer_elapsed: int = 0
```

### FastAPI startup
```python
@app.on_event("startup")
async def startup():
    global EVENT_QUEUE, MAIN_LOOP
    EVENT_QUEUE = asyncio.Queue()
    MAIN_LOOP = asyncio.get_running_loop()
    asyncio.create_task(broadcast_events())
```

### Async → sync bridge
```python
def enqueue(event: dict):
    asyncio.run_coroutine_threadsafe(EVENT_QUEUE.put(event), MAIN_LOOP)
```

### WebSocket events (backend → frontend)
```json
{"type": "state_update", "escalation": 4, "max": 10, "scene": "...", "status": "active"}
{"type": "action_detected", "action_type": "...", "desc": "...", "point_change": -1}
{"type": "game_over", "status": "success"|"fail", "reason": "..."}
{"type": "transcript_update", "role": "student"|"patient", "content": "..."}
{"type": "timer", "elapsed": 60, "limit": 300}
```

### WebSocket events (frontend → backend)
```json
{"type": "begin"}   → set status=active, start timer coroutine
{"type": "reset"}   → reset_game(), clear CONVERSATION_HISTORY
```

### FastRTC response() handler
```python
def response(audio, session_id, chatbot=None):
    if GAME_STATE.status != "active":
        return
    text = STT.transcribe(audio)
    if not text.strip():
        return
    enqueue({"type": "transcript_update", "role": "student", "content": text})
    # System Agent
    detected = run_system_agent(text, GAME_STATE.escalation)
    apply_actions(detected)           # mutates GameState, enqueues state_update + action events
    if check_terminal():              # enqueues game_over if win/loss
        return
    # Patient Agent
    escalation_ctx = f"[CURRENT ESCALATION: {GAME_STATE.escalation}/{SCENARIO['point_bar']['max']}]"
    reply = LLM.chat([{"role":"system","content":escalation_ctx}] + CONVERSATION_HISTORY, PATIENT_PROMPT)
    CONVERSATION_HISTORY.append({"role": "assistant", "content": reply})
    enqueue({"type": "transcript_update", "role": "patient", "content": reply})
    for chunk in TTS.stream_tts_sync(reply, SCENARIO["speech"]):
        yield chunk
```

### FastRTC stream config
```python
stream = Stream(
    modality="audio", mode="send-receive",
    handler=ReplyOnPause(response, input_sample_rate=16000, algo_options=algo_options),
    concurrency_limit=1,   # serialize all audio; avoids race on global GameState
    ui_args={"title": "MEWAI Patient"},
)
```

### Mounting order (critical)
```python
app = FastAPI()
# define all routes on app first (/, /ws, /scenes, /scenario)
app.mount("/scenes", StaticFiles(directory="scenes"), name="scenes")
app = gr.mount_gradio_app(app, stream.ui, path="/gradio")  # Gradio last
```

### Timer (async coroutine, spawned on "begin")
```python
async def run_timer():
    limit = SCENARIO.get("time_limit", 300)
    while GAME_STATE.status == "active":
        await asyncio.sleep(1)
        elapsed = int(time.time() - GAME_STATE.timer_start)
        GAME_STATE.timer_elapsed = elapsed
        await EVENT_QUEUE.put({"type": "timer", "elapsed": elapsed, "limit": limit})
        if elapsed >= limit:
            GAME_STATE.status = "fail"
            GAME_STATE.current_scene = SCENARIO["fail"]
            await EVENT_QUEUE.put({"type": "game_over", "status": "fail", "reason": "Time limit reached"})
            return
```

---

## Frontend (static/index.html)

Single HTML file, inline CSS/JS, no build step.

### 4 screens (CSS show/hide)
- **Start**: background.jpg + title + "Start" button  
- **Intro**: start.jpg + `scenario.intro` text + `scenario.goal` + "Begin" button  
- **Game**: scene img + escalation bar (color-coded) + action badge (3s flash) + transcript sidebar + Gradio iframe  
- **End**: success/fail img + stubbed results (actions taken checklist) + "Play Again" button  

### Key frontend behaviors
- On "Start": fetch `GET /scenario`, show Intro screen
- On "Begin": inject Gradio iframe into `#gradio-container`, send `{type:"begin"}` over WS
- On `state_update`: update scene img src, escalation bar width + color
- On `action_detected`: flash badge (green for negative point_change, red for positive)
- On `game_over`: show End screen, populate actions taken vs missed
- On "Play Again": remove Gradio iframe, send `{type:"reset"}`, show Start screen

### Escalation bar colors
- < 30%: green (`#22c55e`) | 30–60%: yellow | 60–80%: orange | ≥ 80%: red (`#ef4444`)

---

## Prompt Design

### resources/system.txt
- Instructs LLM to analyze one student utterance and return only valid JSON
- Input via user message: `{"utterance": "...", "escalation": N, "actions": [...]}`
- Output: `{"detected_actions": [{"type": "exact type string", "point_change": N, "scene_change": "file.jpg"}]}`
- **Must include**: exact action type strings from scenario (copy verbatim), 4 few-shot examples (2 positive, 1 negative/multi-action, 1 no-detection), instruction to strip markdown fences
- Negative actions (Force IV, Authoritative tone, Restraint) get a dedicated section with examples
- Dedup rule: one credit per action type per utterance

### resources/patient.txt
- Base prompt for `build_patient_prompt()` (same interpolation as simulation.py: `{patient_name}` placeholder)
- Escalation behavior table: 9-10 (single word refusals), 6-8 (guarded/skeptical), 3-5 (brief answers), 1-2 (settling), 0 (fully cooperative + HPI available)
- Locked information gate: "Do not reveal locked information unless escalation is 0"
- Response length: 1–4 sentences max; shorter at higher escalation
- Caregiver (Linda) can be referenced at high escalation for realism

### resources/patient.json
```json
{
  "demographics": {"name": "Jordan", "date_of_birth": "2003-09-14", "sex": "male", "gender": "male", "background": "22yo with ASD level 2, lives with parents"},
  "behavior": "Agitated, hypersensitive to noise/light/touch, limited eye contact, responds well to predictability and choices, escalates with authoritative tone or unannounced procedures. Caregiver (mother Linda) present.",
  "chief_concern": "Abdominal pain, onset 6 hours ago, currently too distressed to give full history",
  "free_information": ["Name is Jordan", "Has abdominal pain", "Scared/overwhelmed by ED", "IV attempt hurt and was unannounced", "Caregiver is mom Linda"],
  "locked_information": ["Pain started 6h ago after eating", "Diffuse lower abdominal pain", "Nausea, no vomiting", "No BM in 2 days", "Similar milder episode 3mo ago resolved spontaneously", "No fever at home", "No regular medications"]
}
```

---

## Known Implementation Challenges

1. **`gr.mount_gradio_app` route precedence**: Define all FastAPI routes before the `gr.mount_gradio_app` call. Serve `index.html` at `/` via `FileResponse`, not `StaticFiles`, to avoid Gradio capturing `/`.

2. **System agent JSON parsing**: Claude Haiku sometimes wraps output in markdown fences. Wrap parse in try/except, strip ` ```json ` fences before parsing. On failure, log raw response and return empty detected actions.

3. **Gradio iframe auto-mic**: Student may interact with Gradio's own buttons before "Begin". The `if GAME_STATE.status != "active": return` guard discards early audio silently.

4. **Sync thread → async queue**: `asyncio.run_coroutine_threadsafe(queue.put(event), MAIN_LOOP)`. `MAIN_LOOP` must be captured in the startup event handler, not at import time.

---

## Verification

```bash
# 1. Setup
cp .env.example .env  # fill in OPENROUTER_API_KEY, FIREWORKS_API_KEY, INWORLD_API_KEY
pip install -r requirements.txt

# 2. Run
python app.py
# Opens at http://localhost:7860

# 3. Manual test flow
# - Click Start → verify start.jpg and intro text appear
# - Click Begin → verify Gradio audio iframe loads, escalation bar shows 5/10
# - Speak: "I can see this is really overwhelming for you" → expect "Acknowledge distress" badge, escalation drops to 4
# - Speak: "Let me ask some staff to step out and dim the lights" → expect "Environmental" badge, escalation drops to 2
# - Speak: "Can I ask your mom to come help calm you?" → expect "Caregiver involvement", escalation hits 0 → success screen
# - Verify patient responds with escalation-appropriate tone throughout
# - Test failure: "We need to get this IV in right now" → expect "Force IV" + "Authoritative tone", escalation spikes
# - Test Play Again: resets state, removes iframe, returns to Start screen
```
