import os
import time
import tempfile
import requests
import json

import gradio as gr
import numpy as np
import soundfile as sf
from dotenv import load_dotenv
from fastapi import FastAPI
from fastrtc import (
    AdditionalOutputs,
    ReplyOnPause,
    Stream,
    AlgoOptions,
    get_twilio_turn_credentials,
    get_tts_model,
    KokoroTTSOptions
)
from gradio.utils import get_space
from numpy.typing import NDArray
import base64


load_dotenv('.env')
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
FIREWORKS_API_KEY = os.getenv("FIREWORKS_API_KEY")
INWORLD_API_KEY = os.getenv("INWORLD_API_KEY")

PATHS = {
    "convo_base": "./resources/simulate.txt",
    "convo_sum": "./resources/summarize.txt",
    "patient": "./resources/patient.json"
}
with open(PATHS["convo_base"], "r", encoding="utf8") as base_file:
    BASE_PROMPT = base_file.read()
# with open(PATHS["convo_sum"], "r", encoding="utf8") as sum_file:
#     SUM_PROMPT = sum_file.read()
with open(PATHS["patient"], "r", encoding="utf8") as patient_file:
    PATIENT = json.load(patient_file)


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

CONVO_PROMPT = build_patient_prompt(BASE_PROMPT, PATIENT)
# print(CONVO_PROMPT)


# STT

class WhisperSTT:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.url = "https://audio-prod.api.fireworks.ai/v1/audio/transcriptions"
    
    def transcribe(self, audio: tuple[int, NDArray[np.int16 | np.float32]]) -> str:
        sr, arr = audio
        # Expecting mono. If shape is (1, N), squeeze to (N,)
        if arr.ndim > 1:
            arr = np.squeeze(arr, axis=0)

        # Ensure int16 PCM for WAV
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
                data={
                    "model": "whisper-v3",
                    "temperature": "0",
                    "vad_model": "silero"
                },
            )
        if response.status_code == 200:
            output = response.json()
            return output['text']

        else:
            raise Exception(f"Transcription failed: {response.status_code} - {response.text}")
            
STT = WhisperSTT(FIREWORKS_API_KEY)


# LLM
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
        data = resp.json()
        return data["choices"][0]["message"]["content"]

LLM = OpenRouterChat()


# TTS
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
                "speakingRate": options['speed']
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
                # Decode if bytes
                if isinstance(line, bytes):
                    line = line.decode('utf-8')
                chunk = json.loads(line)

                audio_chunk = base64.b64decode(chunk["result"]["audioContent"])
                
                # Skip WAV header (44 bytes)
                if len(audio_chunk) > 44:
                    pcm = audio_chunk[44:]
                    # Convert raw bytes (16-bit signed) to numpy array
                    waveform = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
                    yield (sample_rate, waveform)
                    
            except json.JSONDecodeError as e:
                print(f"JSON decode error: {e}, Line content: {line}")
                continue
            except Exception as e:
                print(f"Error processing chunk: {e}, Line: {line}")
                continue

TTS = InworldTTS()
tts_options = {
    'voice': 'Craig',
    'speed': 1.0
}


# FastRTC
def response(audio: tuple[int, NDArray[np.int16 | np.float32]], session_id: str | None,chatbot: list[dict] | None = None):
    print(audio)
    
    chatbot = chatbot or []
    messages = [{"role": d["role"], "content": d["content"]} for d in chatbot]

    # ASR
    t0 = time.time()
    text = STT.transcribe(audio)
    print("transcription time (s):", round(time.time() - t0, 3))
    print("user:", text)

    if not text.strip():
        return

    chatbot.append({"role": "user", "content": text})
    yield AdditionalOutputs(chatbot)
    messages.append({"role": "user", "content": text})

    # LLM
    t1 = time.time()
    response_text = LLM.chat(messages, CONVO_PROMPT)
    print("llm time (s):", round(time.time() - t1, 3))
    print("assistant:", response_text)

    chatbot.append({"role": "assistant", "content": response_text})

    # TTS: synchronous streaming
    for audio_out in TTS.stream_tts_sync(response_text, options=tts_options):
        # audio_out is (sample_rate, np.ndarray) and can be yielded directly
        yield audio_out

    yield AdditionalOutputs(chatbot)

chatbot = gr.Chatbot(type="messages")

# https://fastrtc.org/advanced-configuration/
algo_options = AlgoOptions(
    audio_chunk_duration=1.0,
    started_talking_threshold=0.3,
    speech_threshold=0.3,
)
stream = Stream(
    modality="audio",
    mode="send-receive",
    handler=ReplyOnPause(response, input_sample_rate=16000, algo_options=algo_options),
    additional_outputs_handler=lambda a, b: b,
    additional_inputs=[chatbot],
    additional_outputs=[chatbot],
    rtc_configuration=get_twilio_turn_credentials() if get_space() else None,
    concurrency_limit=5 if get_space() else None,
    time_limit=90 if get_space() else None,
    ui_args={"title": "LLM Voice Chat"},
)

app = FastAPI()
app = gr.mount_gradio_app(app, stream.ui, path="/")


if __name__ == "__main__":
    os.environ["GRADIO_SSR_MODE"] = "false"
    mode = os.getenv("MODE", "UI")
    if mode == "UI":
        stream.ui.launch(server_port=7860, share=True)
    elif mode == "PHONE":
        stream.fastphone(host="0.0.0.0", port=7860)
    else:
        stream.ui.launch(server_port=7860)