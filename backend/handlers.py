import re
import json
import time

from numpy.typing import NDArray
import numpy as np

from backend.game import (
    GAME_STATE, STATE_LOCK, CONVERSATION_HISTORY, HISTORY_LOCK,
)


# These are injected at startup by app.py
STT = None
SYSTEM_LLM = None
PATIENT_LLM = None
TTS = None
SCENARIO = None
PATIENT_PROMPT = None
SYSTEM_PROMPT = None
enqueue = None  # sync-to-async bridge, set in routes.py


def run_system_agent(text: str, escalation: int) -> list[dict]:
    user_msg = json.dumps({
        "utterance": text,
        "escalation": escalation,
        "actions": SCENARIO["actions"],
    })
    raw = SYSTEM_LLM.chat([{"role": "user", "content": user_msg}], SYSTEM_PROMPT)
    clean = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    try:
        return json.loads(clean).get("detected_actions", [])
    except json.JSONDecodeError:
        print(f"[system_agent] JSON parse error. Raw: {raw!r}")
        return []


def apply_actions(detected: list[dict]):
    action_map = {a["type"]: a for a in SCENARIO["actions"]}
    max_esc = SCENARIO["point_bar"]["max"]
    for item in detected:
        action = action_map.get(item.get("type"))
        if not action:
            continue
        with STATE_LOCK:
            GAME_STATE.escalation = max(0, min(max_esc, GAME_STATE.escalation + action["point_change"]))
            GAME_STATE.action_states[action["type"]] = True
        enqueue({
            "type": "action_detected",
            "action_type": action["type"],
            "desc": action["desc"],
            "point_change": action["point_change"],
        })
    with STATE_LOCK:
        active = [t for t, v in GAME_STATE.action_states.items() if v]
        esc = GAME_STATE.escalation
    enqueue({
        "type": "state_update",
        "escalation": esc,
        "max": max_esc,
        "active_actions": active,
        "status": GAME_STATE.status,
    })


def check_terminal() -> bool:
    goal = SCENARIO["point_bar"]["goal"]
    max_esc = SCENARIO["point_bar"]["max"]
    with STATE_LOCK:
        esc = GAME_STATE.escalation
        if esc <= goal:
            GAME_STATE.status = "success"
            enqueue({"type": "game_over", "status": "success", "reason": "Escalation reduced to goal"})
            return True
        if esc >= max_esc:
            GAME_STATE.status = "fail"
            enqueue({"type": "game_over", "status": "fail", "reason": "Escalation reached maximum"})
            return True
    return False


def response(audio: tuple[int, NDArray[np.int16 | np.float32]], session_id: str | None, chatbot=None):
    if GAME_STATE.status != "active":
        return

    t_start = time.perf_counter()

    with STATE_LOCK:
        for action in SCENARIO["actions"]:
            if not action.get("persist"):
                GAME_STATE.action_states[action["type"]] = False

    text = STT.transcribe(audio)
    t_asr = time.perf_counter()

    if not text.strip():
        return

    print(f"[student] {text}")
    enqueue({"type": "transcript_update", "role": "student", "content": text})

    with HISTORY_LOCK:
        CONVERSATION_HISTORY.append({"role": "user", "content": text})

    detected = run_system_agent(text, GAME_STATE.escalation)
    t_system = time.perf_counter()
    if detected:
        apply_actions(detected)

    if check_terminal():
        return

    max_esc = SCENARIO["point_bar"]["max"]
    escalation_ctx = f"[CURRENT ESCALATION: {GAME_STATE.escalation}/{max_esc}]"
    with HISTORY_LOCK:
        history_snapshot = list(CONVERSATION_HISTORY)
    messages = [{"role": "system", "content": escalation_ctx}] + history_snapshot
    reply = PATIENT_LLM.chat(messages, PATIENT_PROMPT)
    t_patient = time.perf_counter()

    print(f"[patient] {reply}")

    with HISTORY_LOCK:
        CONVERSATION_HISTORY.append({"role": "assistant", "content": reply})

    enqueue({"type": "transcript_update", "role": "patient", "content": reply})

    first_chunk = True
    for chunk in TTS.stream_tts_sync(reply, SCENARIO["speech"]):
        if first_chunk:
            t_tts = time.perf_counter()
            first_chunk = False
        yield chunk

    t_end = time.perf_counter()
    print(
        f"[timing] ASR={t_asr-t_start:.2f}s  "
        f"action-LLM={t_system-t_asr:.2f}s  "
        f"patient-LLM={t_patient-t_system:.2f}s  "
        f"TTS-first-chunk={t_tts-t_patient:.2f}s  "
        f"total={t_end-t_start:.2f}s"
    )
