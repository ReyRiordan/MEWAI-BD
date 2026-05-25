# MEWAI — Behavioral De-escalation Simulation

## What This Is

A voice-to-voice medical training simulation where medical students practice behavioral de-escalation with an AI patient. The student speaks aloud; their words are transcribed, analyzed for de-escalation actions, and used to drive a patient AI response. An escalation bar tracks patient agitation. The goal is to reduce it to zero before time runs out.

Stack: **FastAPI + FastRTC + Gradio** (backend), **vanilla JS + HTML/CSS** (frontend), deployed as a single `python3 app.py` process.

## How to Run

```
pip install -r requirements.txt
# Add API keys to .env (see .env section below)
python3 app.py
# Visit http://localhost:7860
```

### Required .env keys
```
OPENROUTER_API_KEY   # Claude Haiku via OpenRouter (system + patient agents)
TOGETHER_API_KEY     # Parakeet STT via Together AI
INWORLD_API_KEY      # Inworld TTS (streaming audio)
```

## File Map

| File | Responsibility |
|------|---------------|
| `app.py` | Entry point. Loads env/resources, instantiates AI clients, wires modules together, starts uvicorn. |
| `backend/agents.py` | Three AI wrapper classes: `ParakeetSTT`, `OpenRouterChat`, `InworldTTS`. No game logic. |
| `backend/game.py` | `GameState` dataclass + module-level singletons (`GAME_STATE`, `STATE_LOCK`, `CONVERSATION_HISTORY`, `HISTORY_LOCK`). Also contains `load_scenario()` and `load_patient_prompt()`. |
| `backend/handlers.py` | Core simulation loop. Per-turn pipeline: STT → system agent → apply actions → check terminal → patient agent → TTS. Depends on singletons injected from `app.py`. |
| `backend/routes.py` | FastAPI endpoints (`/`, `/scenario`, `/ws`), WebSocket broadcast loop, timer coroutine, `reset_game()`. Registers all routes via `register_routes(app, scenario)`. |
| `frontend/index.html` | HTML skeleton — 4 screens (start, intro, game, end). |
| `frontend/style.css` | All UI styles: dark theme, escalation bar colors, badge animation, transcript bubbles. |
| `frontend/app.js` | Frontend logic: WebSocket client, event handlers, screen transitions, DOM updates. |
| `resources/scenario_1.json` | Scenario config (actions, escalation bar, time limit, scene filenames, TTS settings). |
| `resources/patient.txt` | System prompt for the patient AI agent. |
| `resources/patient.json` | Patient case file (demographics, free/locked information). |
| `resources/system.txt` | System prompt for the system (referee) AI agent. |
| `scenes/*.jpg` | Background images that change based on detected actions. |

## Key Architectural Patterns

### Thread-safe state + async bridge
FastRTC runs the audio handler (`handlers.response`) in a sync thread. Game state is protected by `STATE_LOCK` and `HISTORY_LOCK` (threading.Lock). WebSocket broadcast is async. `enqueue()` in `routes.py` bridges the two worlds via `asyncio.run_coroutine_threadsafe`.

### WebSocket event types
All real-time frontend updates flow through `/ws`. The backend pushes these event types:
- `state_update` — new escalation value, scene filename, game status
- `action_detected` — action type, description, point change (triggers badge flash)
- `timer` — elapsed/limit in seconds
- `game_over` — status (success/fail), reason string
- `transcript_update` — role (student/patient), content string

Frontend sends:
- `begin` — starts the simulation (called when student clicks "Begin Simulation")
- `reset` — resets game state (called on "Play Again")

### Two AI agents, one LLM class
Both agents use `OpenRouterChat` (Claude Haiku via OpenRouter) but with different system prompts and input formats:
- **System agent** (`resources/system.txt`): receives JSON `{utterance, escalation, actions}`, returns JSON `{detected_actions: [{type, point_change, scene_change}]}`. Detects which scenario actions the student performed.
- **Patient agent** (`resources/patient.txt`): receives conversation history + escalation context as a system message. Responds in character. Verbosity and cooperativeness scale with escalation level.

### Escalation as the central mechanic
`GAME_STATE.escalation` (int, 0–10) controls:
- Patient response style (high = terse/hostile, low = cooperative)
- What patient information is revealed (locked info only at escalation = 0)
- Win/loss: reaches 0 → success; reaches 10 → fail; timer hits 0 → fail
- Scene image shown (actions trigger `scene_change` in scenario JSON)

## Scenario File Format (`resources/scenario_1.json`)

```json
{
  "speech": { "voice": "Mark", "speed": 1.2 },
  "intro": "...",
  "goal": "...",
  "background": "background.jpg",
  "start": "start.jpg",
  "success": "success.jpg",
  "fail": "fail.jpg",
  "point_bar": { "min": 0, "max": 10, "start": 5, "goal": 0 },
  "time_limit": 300,
  "actions": [
    {
      "type": "caregiver_involvement",
      "desc": "Involve or acknowledge the patient's caregiver",
      "point_change": -3,
      "scene_change": "caregiver_involvement.jpg"
    }
  ]
}
```

Positive `point_change` = bad action (escalates). Negative = good (de-escalates). The system agent returns `type` strings that must exactly match entries in `actions`.
