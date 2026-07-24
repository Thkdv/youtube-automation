"""
Phase 2A — Full pipeline orchestration.
Background video + title → Claude script → ElevenLabs voiceover → FFmpeg assembly → final MP4.
"""
import os

from .script_generator import generate_script
from .video_assembler import assemble_video, generate_black_background
from .voiceover import generate_voiceover


def run_phase2a(
    title: str,
    channel_name: str,
    niche: str,
    background_video_path: str = None,
    output_dir: str = ".",
    duration_minutes: int = 3,
    music_path: str = None,
) -> dict:
    slug = "".join(c if c.isalnum() else "_" for c in title[:40]).strip("_")

    script_data = generate_script(channel_name, niche, title, duration_minutes=duration_minutes)

    audio_path = os.path.join(output_dir, f"{slug}_voiceover.mp3")
    generate_voiceover(script_data["script"], audio_path)

    if background_video_path is None:
        background_video_path = os.path.join(output_dir, f"{slug}_background.mp4")
        generate_black_background(duration_minutes * 60 + 30, background_video_path)

    final_path = os.path.join(output_dir, f"{slug}_final.mp4")
    assemble_video(background_video_path, audio_path, final_path, music_path=music_path)

    return {
        "final_video_path": final_path,
        "title":            script_data["title"],
        "description":      script_data["description"],
        "tags":             script_data["tags"],
    }
