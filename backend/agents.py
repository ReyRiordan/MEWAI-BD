import os
import json
import base64
import tempfile

import numpy as np
import soundfile as sf
import requests
from numpy.typing import NDArray


class ParakeetSTT:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.url = "https://api.together.ai/v1/audio/transcriptions"

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
                data={"model": "nvidia/parakeet-tdt-0.6b-v3", "language": "en"},
            )
        if response.status_code == 200:
            return response.json()['text']
        else:
            raise Exception(f"Transcription failed: {response.status_code} - {response.text}")


class OpenRouterChat:
    def __init__(self, api_key: str,
                 model: str = "anthropic/claude-haiku-4.5",
                 effort: str = "none"):
        self.api_key = api_key
        self.model = model
        self.effort = effort
        self.url = "https://openrouter.ai/api/v1/chat/completions"

    def chat(self, messages: list[dict], system_prompt: str) -> str:
        payload = {
            "model": self.model,
            "reasoning": {"effort": self.effort},
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


class InworldTTS:
    def __init__(self, api_key: str):
        self.api_key = api_key
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
