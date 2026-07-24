"""
Phase 2A — Script generation via Claude API.
Generates YouTube video scripts from channel config and topic.
"""
import os
import json
import anthropic
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def generate_script(channel_name: str, niche: str, topic: str, duration_minutes: int = 10) -> dict:
    """
    Generate a YouTube video script for a given channel and topic.

    Returns:
        {
            "title": str,
            "description": str,
            "tags": list[str],
            "script": str
        }
    """
    prompt = f"""You are a YouTube scriptwriter specializing in {niche} content.

Write a complete, engaging YouTube video script for the channel "{channel_name}".

Topic: {topic}
Target duration: {duration_minutes} minutes (approximately {duration_minutes * 130} words spoken)

Rules:
- Title must NOT mention any duration (no "10 hours", "1 hour", "minutes", etc.)
- Script must be plain spoken words only — no stage directions, no brackets, no [PAUSE], no [EMPHASIS], no cues of any kind
- Write naturally as if speaking directly to the viewer

Return your response as a JSON object with exactly these keys:
- title: compelling YouTube video title (max 70 characters, no duration mention)
- description: YouTube video description (150-300 words, include keywords)
- tags: list of 10-15 relevant tags
- script: the full spoken script, plain text only

JSON only, no other text."""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    return json.loads(raw)


if __name__ == "__main__":
    result = generate_script(
        channel_name="Sleep Hub",
        niche="sleep and relaxation",
        topic="10 minutes of calming rain sounds for deep sleep",
        duration_minutes=10
    )
    print(json.dumps(result, indent=2))
