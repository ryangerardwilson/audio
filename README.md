# audio

CLI for listing and switching Linux audio output devices via `pactl` (PulseAudio/PipeWire).

## Requirements

- Linux
- `pactl` available in `PATH` (from PulseAudio/PipeWire tools)

## Usage

Run interactively:

```bash
audio
```

List available output targets:

```bash
audio --list
```

Set output target directly:

```bash
audio --set <sink>
audio --set <sink:port>
```

Show version / upgrade:

```bash
audio -v
audio -u
```

Examples:

```bash
audio --set alsa_output.pci-0000_00_1f.3.analog-stereo
audio --set alsa_output.pci-0000_00_1f.3.analog-stereo:analog-output-headphones
```

## Bluetooth pairing

Pairing is done manually with `bluetoothctl` (also shown in `audio -h`):

```bash
bluetoothctl
power on
agent on
default-agent
scan on
# put speaker in pairing mode now
pair <MAC>
trust <MAC>
connect <MAC>
```

After connecting the speaker, run `audio` and choose the listed Bluetooth device (for example `Stone 180 -> Headset`).

## Install from releases

Installer script (expects repo `ryangerardwilson/audio`):

```bash
curl -fsSL https://raw.githubusercontent.com/ryangerardwilson/audio/main/install.sh | bash
```

Install a specific release:

```bash
curl -fsSL https://raw.githubusercontent.com/ryangerardwilson/audio/main/install.sh | bash -s -- --version 0.1.0
```

After install, run:

```bash
audio -h
```

## Release automation

GitHub Actions workflow builds a Linux x86_64 binary tarball on tags matching `v*` and uploads it as a release asset:

- `.github/workflows/release.yml`

## Files

- `main.py`: CLI entrypoint
- `install.sh`: release installer
