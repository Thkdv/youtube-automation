"""
Phase 2A — Voiceover generation via ElevenLabs API.
Converts a script string to an MP3 file using the Adam voice.
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs

load_dotenv()

ADAM_VOICE_ID = "pNInz6obpgDQGcFmaJgB"

client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))


def generate_voiceover(script: str, output_path: str) -> str:
    audio = client.text_to_speech.convert(
        voice_id=ADAM_VOICE_ID,
        text=script,
        model_id="eleven_turbo_v2_5",
        output_format="mp3_44100_128",
    )

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with open(out, "wb") as f:
        for chunk in audio:
            f.write(chunk)

    return str(out)


if __name__ == "__main__":
    test_script = "This is a test of the voiceover system. The audio pipeline is working correctly."
    out = generate_voiceover(test_script, "output/test_voiceover.mp3")
    print(f"Voiceover saved to: {out}")
