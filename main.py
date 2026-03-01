#!/usr/bin/env python3
"""CLI to list and switch audio output devices (PulseAudio/PipeWire via pactl)."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Sink:
    name: str
    description: str
    state: str
    is_default: bool = False
    active_port: Optional[str] = None
    ports: list["SinkPort"] = field(default_factory=list)


@dataclass
class SinkPort:
    name: str
    description: str
    availability: str = "unknown"
    is_active: bool = False


@dataclass
class OutputOption:
    sink_name: str
    sink_description: str
    state: str
    port_name: Optional[str] = None
    port_description: Optional[str] = None
    is_active: bool = False

    @property
    def target(self) -> str:
        if self.port_name:
            return f"{self.sink_name}:{self.port_name}"
        return self.sink_name

    @property
    def label(self) -> str:
        if self.port_description:
            return f"{self.sink_description} -> {self.port_description}"
        return self.sink_description


def run_command(cmd: List[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=False, text=True, capture_output=True)


def require_pactl() -> None:
    if shutil.which("pactl") is None:
        print("Error: 'pactl' was not found in PATH.", file=sys.stderr)
        print(
            "Install PulseAudio/PipeWire tools and try again.",
            file=sys.stderr,
        )
        sys.exit(1)


def get_default_sink_name() -> str:
    result = run_command(["pactl", "info"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Failed to run 'pactl info'")
    for line in result.stdout.splitlines():
        if line.startswith("Default Sink:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError("Could not determine default sink from 'pactl info'")


def _parse_active_port_name(active_port: object) -> Optional[str]:
    if isinstance(active_port, dict):
        return active_port.get("name")
    if isinstance(active_port, str):
        return active_port
    return None


def parse_sinks_from_json(default_sink: str) -> list[Sink]:
    result = run_command(["pactl", "-f", "json", "list", "sinks"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Failed to list sinks in JSON mode")

    raw_sinks = json.loads(result.stdout)
    sinks: list[Sink] = []
    for sink in raw_sinks:
        name = sink.get("name", "")
        props = sink.get("properties", {})
        description = props.get("device.description", name)
        state = sink.get("state", "UNKNOWN")
        active_port = _parse_active_port_name(sink.get("active_port"))
        ports: list[SinkPort] = []
        for port in sink.get("ports", []):
            port_name = port.get("name")
            if not port_name:
                continue
            port_desc = port.get("description", port_name)
            availability = str(port.get("availability", "unknown")).lower()
            ports.append(
                SinkPort(
                    name=port_name,
                    description=port_desc,
                    availability=availability,
                    is_active=(port_name == active_port),
                )
            )
        sinks.append(
            Sink(
                name=name,
                description=description,
                state=state,
                is_default=(name == default_sink),
                active_port=active_port,
                ports=ports,
            )
        )
    return sinks


def parse_sinks_from_short(default_sink: str) -> list[Sink]:
    result = run_command(["pactl", "list", "short", "sinks"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Failed to list sinks")

    sinks: list[Sink] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split()
        # Expected: id, name, ..., state
        if len(parts) < 2:
            continue
        name = parts[1]
        state = parts[-1] if parts else "UNKNOWN"
        sinks.append(
            Sink(
                name=name,
                description=name,
                state=state,
                is_default=(name == default_sink),
                ports=[],
            )
        )
    return sinks


def get_sinks() -> list[Sink]:
    default_sink = get_default_sink_name()
    try:
        sinks = parse_sinks_from_json(default_sink)
        if sinks:
            return sinks
    except Exception:
        pass
    return parse_sinks_from_short(default_sink)


def build_output_options(sinks: list[Sink]) -> list[OutputOption]:
    options: list[OutputOption] = []
    for sink in sinks:
        ports = sink.ports or []
        if ports:
            for port in ports:
                options.append(
                    OutputOption(
                        sink_name=sink.name,
                        sink_description=sink.description,
                        state=sink.state,
                        port_name=port.name,
                        port_description=port.description,
                        is_active=(sink.is_default and port.is_active),
                    )
                )
            continue
        options.append(
            OutputOption(
                sink_name=sink.name,
                sink_description=sink.description,
                state=sink.state,
                is_active=sink.is_default,
            )
        )
    return options


def print_options(options: list[OutputOption]) -> None:
    if not options:
        print("No output devices found.")
        return

    print("Available audio output devices:\n")
    for idx, option in enumerate(options, start=1):
        active_tag = " [active]" if option.is_active else ""
        print(f"{idx}. {option.label}{active_tag}")
        print(f"   target: {option.target}")
        print(f"   sink: {option.sink_name}")
        print(f"   state: {option.state}")


def move_current_streams_to_sink(sink_name: str) -> None:
    result = run_command(["pactl", "list", "short", "sink-inputs"])
    if result.returncode != 0:
        return
    for line in result.stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        input_id = parts[0]
        run_command(["pactl", "move-sink-input", input_id, sink_name])


def set_output_target(target: str, sinks: list[Sink], options: list[OutputOption]) -> None:
    sink_name: str
    port_name: Optional[str] = None

    sink_names = {sink.name for sink in sinks}
    option_targets = {option.target for option in options}

    if target in option_targets and ":" in target:
        sink_name, port_name = target.split(":", 1)
    elif target in sink_names:
        sink_name = target
    elif ":" in target:
        candidate_sink, candidate_port = target.split(":", 1)
        if candidate_sink not in sink_names:
            raise ValueError(f"Sink '{candidate_sink}' not found.")
        sink_name = candidate_sink
        port_name = candidate_port
    else:
        raise ValueError(f"Target '{target}' not found.")

    result = run_command(["pactl", "set-default-sink", sink_name])
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or f"Failed to set sink '{sink_name}'"
        )

    if port_name:
        port_result = run_command(["pactl", "set-sink-port", sink_name, port_name])
        if port_result.returncode != 0:
            raise RuntimeError(
                port_result.stderr.strip()
                or f"Failed to set port '{port_name}' on sink '{sink_name}'"
            )

    move_current_streams_to_sink(sink_name)
    if port_name:
        print(f"Audio output set to: {sink_name}:{port_name}")
    else:
        print(f"Default audio output set to: {sink_name}")


def choose_option_interactively(options: list[OutputOption]) -> str:
    if not options:
        raise RuntimeError("No output options are available to choose from.")

    while True:
        raw = input("\nChoose device number (or 'q' to quit): ").strip().lower()
        if raw in {"q", "quit", "exit"}:
            raise KeyboardInterrupt
        if not raw.isdigit():
            print("Please enter a valid number.")
            continue
        idx = int(raw)
        if 1 <= idx <= len(options):
            return options[idx - 1].target
        print(f"Please choose a number between 1 and {len(options)}.")


def build_parser() -> argparse.ArgumentParser:
    pairing_help = """Bluetooth pairing (manual, outside this app):
  bluetoothctl
  power on
  agent on
  default-agent
  scan on
  # put speaker in pairing mode now
  pair <MAC>
  trust <MAC>
  connect <MAC>
"""
    parser = argparse.ArgumentParser(
        description="List and switch audio output devices (sinks).",
        epilog=pairing_help,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List output devices and exit.",
    )
    parser.add_argument(
        "--set",
        metavar="TARGET",
        help="Set output by sink name or sink:port (see --list).",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    require_pactl()

    try:
        sinks = get_sinks()
        options = build_output_options(sinks)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.list:
        print_options(options)
        return 0

    if args.set:
        try:
            set_output_target(args.set, sinks, options)
            return 0
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    print_options(options)
    try:
        chosen = choose_option_interactively(options)
    except KeyboardInterrupt:
        print("\nNo changes made.")
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        set_output_target(chosen, sinks, options)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
