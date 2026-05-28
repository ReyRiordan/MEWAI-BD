import os

import gradio as gr
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastrtc import ReplyOnPause, Stream, AlgoOptions

from backend.agents import ParakeetSTT, OpenRouterChat, InworldTTS
from backend.game import load_scenario, load_patient_prompt
from backend import handlers
from backend.routes import register_routes


load_dotenv('.env')

SCENARIO = load_scenario("resources/scenario_1.json")
PATIENT_PROMPT = load_patient_prompt("resources/patient.txt", "resources/patient.json")
SYSTEM_PROMPT = open("resources/system.txt", encoding="utf8").read()

STT = ParakeetSTT(os.getenv("TOGETHER_API_KEY"))
LLM = OpenRouterChat(os.getenv("OPENROUTER_API_KEY"))
TTS = InworldTTS(os.getenv("INWORLD_API_KEY"))

# Inject singletons into handlers module
handlers.STT = STT
handlers.LLM = LLM
handlers.TTS = TTS
handlers.SCENARIO = SCENARIO
handlers.PATIENT_PROMPT = PATIENT_PROMPT
handlers.SYSTEM_PROMPT = SYSTEM_PROMPT

algo_options = AlgoOptions(
    audio_chunk_duration=1.0,
    started_talking_threshold=0.3,
    speech_threshold=0.3,
)
stream = Stream(
    modality="audio",
    mode="send-receive",
    handler=ReplyOnPause(handlers.response, input_sample_rate=16000, algo_options=algo_options),
    concurrency_limit=1,
    ui_args={"title": "MEWAI Patient"},
)

app = FastAPI()
app.mount("/scenes", StaticFiles(directory="scenes"), name="scenes")
app.mount("/visuals", StaticFiles(directory="visuals"), name="visuals")
app.mount("/frontend", StaticFiles(directory="frontend"), name="frontend")
app = gr.mount_gradio_app(app, stream.ui, path="/gradio")

register_routes(app, SCENARIO)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
