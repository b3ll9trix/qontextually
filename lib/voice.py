"""Voice-out for the Qontextually agent via Gradium TTS.

Given a text string, synthesizes speech via Gradium and plays it through the
default audio sink (headset, laptop speakers, whatever the OS has).

Two entry points:

1. Module CLI for ad-hoc speech:
       python -m lib.voice --text "Hello world"
       python -m lib.voice --file some_text.txt
       python -m lib.voice --text "hi" --save out.wav   # synth but don't play

2. Library API consumed by lib.agent:
       from lib.voice import speak
       await speak("Inazuma complies with German law.")

Requires GRADIUM_API_KEY in .env.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console

GRADIUM_VOICE_ID = os.environ.get("GRADIUM_VOICE_ID", "YTpq7expH9539ERJ")


async def synthesize(text: str, voice_id: str | None = None) -> bytes:
    """Call Gradium TTS and return raw WAV bytes. Uses GRADIUM_VOICE_ID env
    (falls back to a vetted default voice) when no voice_id is passed."""
    import gradium

    client = gradium.client.GradiumClient()
    result = await client.tts(
        setup={"voice_id": voice_id or GRADIUM_VOICE_ID, "output_format": "wav"},
        text=text,
    )
    return result.raw_data


def _pick_playback_sink() -> str | None:
    """Pick a non-Bluetooth Pulse/PipeWire sink to avoid wedged BT sinks.

    Bluetooth sinks can enter a suspended state from which paplay blocks
    indefinitely on ACK. Wired analog output is always reliable. Returns the
    first `alsa_output.*analog` sink name, or None if pactl isn't available."""
    override = os.environ.get("QONTEXT_AUDIO_SINK")
    if override:
        return override
    if not shutil.which("pactl"):
        return None
    try:
        out = subprocess.run(["pactl", "list", "short", "sinks"], capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return None
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1].startswith("alsa_output.") and "analog" in parts[1]:
            return parts[1]
    return None


def _play(wav_path: Path) -> int:
    """Play a WAV through the first playback tool available.

    Prefers paplay with an explicit wired sink (bypasses wedged BT sinks),
    falls back to aplay (direct ALSA) then ffplay. Returns the player exit code."""
    sink = _pick_playback_sink()
    if shutil.which("paplay"):
        cmd = ["paplay"]
        if sink:
            cmd.append(f"--device={sink}")
        cmd.append(str(wav_path))
        return subprocess.run(cmd).returncode
    for tool, extra in (("aplay", ["-q"]), ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "error"])):
        if shutil.which(tool):
            return subprocess.run([tool, *extra, str(wav_path)]).returncode
    raise RuntimeError("no audio player found (install pulseaudio-utils, alsa-utils, or ffmpeg)")


async def speak(text: str, voice_id: str | None = None, save_to: Path | None = None) -> Path:
    """Synthesize `text` via Gradium, optionally save to `save_to`, play it,
    return the path of the written WAV."""
    wav_bytes = await synthesize(text, voice_id=voice_id)
    out = save_to or Path(tempfile.mkstemp(suffix=".wav")[1])
    out.write_bytes(wav_bytes)
    _play(out)
    return out


def main() -> int:
    load_dotenv(".env")
    parser = argparse.ArgumentParser(description="Voice-out for the Qontextually agent (Gradium TTS)")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--text", help="Text to speak")
    src.add_argument("--file", help="Path to a text file to speak")
    parser.add_argument("--voice-id", help=f"Gradium voice UID (default: {GRADIUM_VOICE_ID})")
    parser.add_argument("--save", help="Save the synthesized WAV to this path")
    parser.add_argument("--no-play", action="store_true", help="Synthesize only; do not play")
    args = parser.parse_args()

    console = Console()
    text = args.text if args.text else Path(args.file).read_text(encoding="utf-8").strip()
    if not text:
        console.print("[red]empty text[/red]")
        return 1

    console.print(f"[dim]Synthesizing via Gradium TTS\u2026 ({len(text)} chars)[/dim]")
    try:
        wav_bytes = asyncio.run(synthesize(text, voice_id=args.voice_id))
    except Exception as exc:
        console.print(f"[red]Gradium TTS failed: {type(exc).__name__}: {exc}[/red]")
        return 1

    out = Path(args.save) if args.save else Path(tempfile.mkstemp(suffix=".wav")[1])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(wav_bytes)
    console.print(f"[dim]wrote {len(wav_bytes):,} bytes \u2192 {out}[/dim]")

    if args.no_play:
        return 0
    try:
        return _play(out)
    except Exception as exc:
        console.print(f"[yellow]playback failed: {exc}[/yellow]")
        return 0


if __name__ == "__main__":
    sys.exit(main())
