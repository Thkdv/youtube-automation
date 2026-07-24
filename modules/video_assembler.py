"""
Phase 2A — Video assembly via FFmpeg.
Combines a background video with a voiceover MP3 into a final MP4.
"""
import re
import subprocess
from pathlib import Path

import imageio_ffmpeg


FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


def generate_black_background(duration_seconds: int, output_path: str, width: int = 1920, height: int = 1080) -> str:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG, "-y",
        "-f", "lavfi",
        "-i", f"color=c=black:size={width}x{height}:rate=30",
        "-t", str(duration_seconds),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed:\n{result.stderr}")
    return str(out)


def assemble_video(video_path: str, audio_path: str, output_path: str, music_path: str = None, music_volume_db: int = -18) -> str:
    """Combine background video with voiceover, optionally mixing in a background
    music track underneath (music_path). Music is looped and ducked to music_volume_db
    so the voiceover stays clearly on top of the mix."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if music_path is None:
        cmd = [
            FFMPEG, "-y",
            "-stream_loop", "-1",
            "-i", video_path,
            "-i", audio_path,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            str(out),
        ]
    else:
        cmd = [
            FFMPEG, "-y",
            "-stream_loop", "-1",
            "-i", video_path,
            "-i", audio_path,
            "-stream_loop", "-1",
            "-i", music_path,
            "-filter_complex",
            f"[2:a]volume={music_volume_db}dB[music_quiet];"
            f"[1:a][music_quiet]amix=inputs=2:duration=first:dropout_transition=0[aout]",
            "-map", "0:v:0",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            str(out),
        ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed:\n{result.stderr}")

    return str(out)


def _has_audio_stream(path: str) -> bool:
    # No ffprobe dependency (imageio_ffmpeg only bundles ffmpeg) — running ffmpeg with no
    # output still prints the full input stream analysis to stderr before erroring out.
    result = subprocess.run([FFMPEG, "-i", path], capture_output=True, text=True)
    return "Audio:" in result.stderr


def _ensure_audio(path: str, tmpdir: Path) -> str:
    """Clips with no audio stream (common for silent B-roll/outro footage) would make the
    concat filter graph in append_clips() fail outright when it references that input's
    missing [i:a] pad — so mux in silence first, reusing the same -shortest pattern already
    used for voiceover/background pairing in assemble_video()."""
    if _has_audio_stream(path):
        return path
    dest = str(tmpdir / f"silent_{Path(path).stem}.mp4")
    cmd = [
        FFMPEG, "-y",
        "-i", path,
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-c:v", "copy",
        "-c:a", "aac",
        "-shortest",
        dest,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed (silence mux):\n{result.stderr}")
    return dest


def _probe_video(path: str):
    """Returns (codec, width, height, fps) for path's video stream, or None if it can't
    be parsed. Same stderr-scraping approach as _has_audio_stream — no ffprobe available."""
    result = subprocess.run([FFMPEG, "-i", path], capture_output=True, text=True)
    m = re.search(r"Video:\s*(\w+).*?(\d{2,5})x(\d{2,5}).*?(\d+(?:\.\d+)?)\s*fps", result.stderr)
    if not m:
        return None
    codec, w, h, fps = m.groups()
    return (codec, int(w), int(h), round(float(fps)))


def _try_fast_concat(inputs: list, output_path: str) -> bool:
    """When every input already shares the same video codec/resolution/fps, concatenation
    doesn't need any re-encoding at all — just repackaging via the concat demuxer's stream
    copy, which is close to instant instead of a full re-encode. Returns False (caller falls
    back to the slow re-encode path) if formats don't match or this attempt fails for any
    reason — this is a speed optimization, not the correctness guarantee, so it's safe to
    bail out of freely."""
    probes = [_probe_video(p) for p in inputs]
    if any(p is None for p in probes) or len(set(probes)) != 1:
        return False

    list_file = Path(output_path).parent / "concat_list.txt"
    list_file.write_text("".join(f"file '{Path(p).resolve()}'\n" for p in inputs))

    cmd = [
        FFMPEG, "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0


def append_clips(video_path: str, clip_paths: list, output_path: str, width: int = 1920, height: int = 1080) -> str:
    """Concatenate video_path followed by each clip in clip_paths, in order. Tries a fast
    stream-copy concat first (see _try_fast_concat) — only safe when every input already
    matches on codec/resolution/fps. Otherwise falls back to re-encoding with scale/pad
    applied to every input, since appended clips — e.g. an outro — aren't guaranteed to
    share the main video's resolution or codec."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    inputs = [_ensure_audio(p, out.parent) for p in [video_path] + list(clip_paths)]
    n = len(inputs)

    if _try_fast_concat(inputs, str(out)):
        print("  Outro   : formats matched — used fast stream-copy (no re-encode)")
        return str(out)

    print("  Outro   : formats didn't match (or fast attempt failed) — re-encoding, this is the slow path")
    cmd = [FFMPEG, "-y"]
    for path in inputs:
        cmd += ["-i", path]

    scale_pad = ";".join(
        f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1[v{i}]"
        for i in range(n)
    )
    concat_refs = "".join(f"[v{i}][{i}:a]" for i in range(n))
    filter_complex = f"{scale_pad};{concat_refs}concat=n={n}:v=1:a=1[outv][outa]"

    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", "[outa]",
        "-c:v", "libx264",
        "-c:a", "aac",
        "-pix_fmt", "yuv420p",
        str(out),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed:\n{result.stderr}")

    return str(out)


if __name__ == "__main__":
    out = assemble_video(
        video_path="output/test_voiceover.mp3",
        audio_path="output/test_voiceover.mp3",
        output_path="output/test_assembled.mp4",
    )
    print(f"Assembled video saved to: {out}")
