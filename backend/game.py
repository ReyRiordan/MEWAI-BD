import json
import threading
from dataclasses import dataclass, field


def load_scenario(path: str = "resources/scenario_1.json") -> dict:
    return json.load(open(path))


def load_patient_prompt(prompt_path: str, case_path: str) -> str:
    base = open(prompt_path, encoding="utf8").read()
    case = json.load(open(case_path))
    return _build_patient_prompt(base, case)


def _build_patient_prompt(base: str, case: dict) -> str:
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


@dataclass
class GameState:
    status: str = "idle"  # idle | active | success | fail
    escalation: int = 5
    current_scene: str = "background.jpg"
    actions_taken: list = field(default_factory=list)
    timer_start: float | None = None
    timer_elapsed: int = 0


# Module-level singletons shared across modules
GAME_STATE = GameState()
STATE_LOCK = threading.Lock()
CONVERSATION_HISTORY: list[dict] = []
HISTORY_LOCK = threading.Lock()
