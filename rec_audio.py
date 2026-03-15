#!/usr/bin/env python3
"""Record from the default audio input source and play it back."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=False, text=True, capture_output=True)


def get_default_source() -> str:
    result = run(["pactl", "info"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Failed to run pactl info")

    for line in result.stdout.splitlines():
        if line.startswith("Default Source:"):
            source = line.split(":", 1)[1].strip()
            if source:
                return source
    raise RuntimeError("Could not determine default source from pactl info")


def get_source_details(source_name: str) -> dict:
    result = run(["pactl", "-f", "json", "list", "sources"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Failed to query sources")
    data = json.loads(result.stdout)
    for source in data:
        if source.get("name") == source_name:
            return source
    raise RuntimeError(f"Source not found: {source_name}")


def parse_active_port(source: dict) -> str | None:
    active = source.get("active_port")
    if isinstance(active, dict):
        name = active.get("name")
        return str(name) if name else None
    if isinstance(active, str):
        return active
    return None


def average_volume_percent(source: dict) -> int | None:
    vol = source.get("volume")
    if not isinstance(vol, dict):
        return None
    values = []
    for channel in vol.values():
        if not isinstance(channel, dict):
            continue
        percent = channel.get("value_percent")
        if isinstance(percent, str) and percent.endswith("%"):
            try:
                values.append(int(percent.rstrip("%")))
            except ValueError:
                pass
    if not values:
        return None
    return sum(values) // len(values)


def set_source_port(source_name: str, port_name: str) -> None:
    result = run(["pactl", "set-source-port", source_name, port_name])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"Failed to set source port: {port_name}")


def set_source_mute(source_name: str, mute: bool) -> None:
    result = run(["pactl", "set-source-mute", source_name, "1" if mute else "0"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Failed to set source mute")


def set_source_volume(source_name: str, percent: int) -> None:
    result = run(["pactl", "set-source-volume", source_name, f"{percent}%"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Failed to set source volume")


def record_with_ffmpeg(source: str, output_path: Path, duration: int) -> None:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "pulse",
        "-i",
        source,
        "-t",
        str(duration),
        str(output_path),
    ]
    result = run(cmd)
    if result.returncode != 0:
        fallback = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "pulse",
            "-i",
            "default",
            "-t",
            str(duration),
            str(output_path),
        ]
        result = run(fallback)
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "ffmpeg recording failed"
            raise RuntimeError(message)


def record_with_parecord(source: str, output_path: Path, duration: int) -> None:
    cmd = [
        "parecord",
        "--device",
        source,
        "--rate=48000",
        "--channels=1",
        "--format=s16le",
        "--file-format=wav",
        str(output_path),
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        proc.wait(timeout=duration)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    if proc.returncode not in (0, -15):
        stderr = proc.stderr.read().strip() if proc.stderr else ""
        raise RuntimeError(stderr or "parecord recording failed")


def record_with_arecord(output_path: Path, duration: int) -> None:
    cmd = [
        "arecord",
        "-D",
        "pulse",
        "-f",
        "S16_LE",
        "-c",
        "1",
        "-r",
        "16000",
        "-d",
        str(duration),
        str(output_path),
    ]
    result = run(cmd)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "arecord recording failed"
        raise RuntimeError(message)


def play_file(path: Path) -> None:
    if shutil.which("ffplay"):
        cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", str(path)]
    elif shutil.which("paplay"):
        cmd = ["paplay", str(path)]
    elif shutil.which("aplay"):
        cmd = ["aplay", str(path)]
    else:
        raise RuntimeError("No playback command found (ffplay/paplay/aplay).")

    result = run(cmd)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "playback failed"
        raise RuntimeError(message)


def record(path: Path, duration: int, source: str, backend: str) -> str:
    if backend == "parecord":
        if not shutil.which("parecord"):
            raise RuntimeError("parecord not found in PATH.")
        record_with_parecord(source, path, duration)
        return "parecord"
    if backend == "ffmpeg":
        if not shutil.which("ffmpeg"):
            raise RuntimeError("ffmpeg not found in PATH.")
        record_with_ffmpeg(source, path, duration)
        return "ffmpeg"
    if backend == "arecord":
        if not shutil.which("arecord"):
            raise RuntimeError("arecord not found in PATH.")
        record_with_arecord(path, duration)
        return "arecord"

    if shutil.which("parecord"):
        record_with_parecord(source, path, duration)
        return "parecord"
    if shutil.which("ffmpeg"):
        record_with_ffmpeg(source, path, duration)
        return "ffmpeg"
    if shutil.which("arecord"):
        record_with_arecord(path, duration)
        return "arecord"
    raise RuntimeError("No recording command found (parecord/ffmpeg/arecord).")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Record from the default input source and play it back.",
    )
    parser.add_argument(
        "-d",
        "--duration",
        type=int,
        default=5,
        help="Recording duration in seconds (default: 5).",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Optional output WAV path. If omitted, a temp file is used.",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep temp recording file (ignored when --output is provided).",
    )
    parser.add_argument(
        "--source",
        help="Optional source name override (defaults to pactl Default Source).",
    )
    parser.add_argument(
        "--port",
        help="Optional source port to set before recording (e.g. analog-input-headset-mic).",
    )
    parser.add_argument(
        "--volume",
        type=int,
        help="Optional source volume percent to set before recording (e.g. 120).",
    )
    parser.add_argument(
        "--no-unmute",
        action="store_true",
        help="Do not auto-unmute the source before recording.",
    )
    parser.add_argument(
        "--backend",
        choices=["auto", "parecord", "ffmpeg", "arecord"],
        default="auto",
        help="Recording backend to use (default: auto).",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.duration <= 0:
        print("Error: --duration must be > 0", file=sys.stderr)
        return 1

    if shutil.which("pactl") is None:
        print("Error: pactl not found in PATH.", file=sys.stderr)
        return 1

    try:
        source = args.source or get_default_source()
        details = get_source_details(source)

        if args.port:
            set_source_port(source, args.port)
            details = get_source_details(source)

        if not args.no_unmute and details.get("mute") is True:
            set_source_mute(source, False)
            details = get_source_details(source)

        if args.volume is not None:
            if args.volume <= 0:
                raise RuntimeError("--volume must be > 0")
            set_source_volume(source, args.volume)
            details = get_source_details(source)

        active_port = parse_active_port(details) or "unknown"
        muted = bool(details.get("mute", False))
        volume = average_volume_percent(details)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    delete_after = False
    if args.output:
        out_path = Path(args.output).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        out_path = Path(tmp.name)
        tmp.close()
        delete_after = not args.keep

    print(f"Source: {source}")
    print(f"Active port: {active_port}")
    print(f"Muted: {'yes' if muted else 'no'}")
    print(f"Volume: {str(volume) + '%' if volume is not None else 'unknown'}")
    print(f"Backend: {args.backend}")
    print(f"Recording {args.duration}s to: {out_path}")

    try:
        used_backend = record(out_path, args.duration, source, args.backend)
        print(f"Recorded with: {used_backend}")
        print("Playing back recording...")
        play_file(out_path)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        if delete_after:
            try:
                out_path.unlink(missing_ok=True)
            except OSError:
                pass

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
