#!/usr/bin/env python3
"""Build the bundled English Bingo callouts using local synthesis tools."""

import argparse
import atexit
from array import array
import ctypes
import ctypes.util
from functools import cache
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys



def call_spec(number):
    """Describe the text for one 75-ball Bingo call."""
    if type(number) is not int or not 1 <= number <= 75:
        raise ValueError("Bingo number must be an integer from 1 through 75")
    letter = "BINGO"[(number - 1) // 15]
    units = (
        "zero one two three four five six seven eight nine ten eleven twelve "
        "thirteen fourteen fifteen sixteen seventeen eighteen nineteen"
    ).split()
    tens = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy")
    words = units[number] if number < 20 else tens[number // 10]
    if number >= 20 and number % 10:
        words += "-" + units[number % 10]
    letter_word = {"B": "bee", "I": "eye", "N": "en", "G": "gee", "O": "oh"}[letter]
    return {
        "number": number,
        "letter": letter,
        "display_text": f"{letter} {number}",
        "spoken_text": f"{letter_word}, {words}",
        "ssml": f'<speak>{letter_word}<break time="200ms"/>{words}</speak>',
        "file": f"{number}.mp3",
    }


@cache
def _speech_engine():
    """Keep the process-global eSpeak engine and its callback alive until exit."""
    library_name = ctypes.util.find_library("espeak-ng")
    if not library_name:
        raise RuntimeError("Generation requires an already-installed libespeak-ng")
    library = ctypes.CDLL(library_name)
    callback_type = ctypes.CFUNCTYPE(
        ctypes.c_int, ctypes.POINTER(ctypes.c_short), ctypes.c_int, ctypes.c_void_p
    )
    library.espeak_Initialize.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    library.espeak_Initialize.restype = ctypes.c_int
    library.espeak_SetSynthCallback.argtypes = [callback_type]
    library.espeak_SetSynthCallback.restype = None
    library.espeak_SetVoiceByName.argtypes = [ctypes.c_char_p]
    library.espeak_SetVoiceByName.restype = ctypes.c_int
    library.espeak_SetParameter.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
    library.espeak_SetParameter.restype = ctypes.c_int
    library.espeak_Synth.argtypes = [
        ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint, ctypes.c_int,
        ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint), ctypes.c_void_p,
    ]
    library.espeak_Synth.restype = ctypes.c_int
    library.espeak_Info.argtypes = [ctypes.POINTER(ctypes.c_char_p)]
    library.espeak_Info.restype = ctypes.c_char_p
    library.espeak_Terminate.argtypes = []
    library.espeak_Terminate.restype = ctypes.c_int

    chunks = []

    @callback_type
    def retrieve(samples, count, _events):
        if samples and count > 0:
            chunks.append(ctypes.string_at(samples, count * ctypes.sizeof(ctypes.c_short)))
        return 0

    # AUDIO_OUTPUT_SYNCHRONOUS (2) delivers PCM to the callback without a device.
    sample_rate = library.espeak_Initialize(2, 0, None, 0)
    if sample_rate <= 0:
        raise RuntimeError("libespeak-ng could not initialize its local voice data")
    try:
        library.espeak_SetSynthCallback(retrieve)
        if library.espeak_SetVoiceByName(b"en-us") != 0:
            raise RuntimeError("The local en-us voice is unavailable")
        # eSpeak parameters: rate=1, volume=2, pitch=3; absolute values.
        for parameter, value in ((1, 145), (2, 100), (3, 50)):
            if library.espeak_SetParameter(parameter, value, 0) != 0:
                raise RuntimeError(f"Could not set eSpeak parameter {parameter}")
    except Exception:
        library.espeak_Terminate()
        raise
    atexit.register(library.espeak_Terminate)
    return library, retrieve, chunks, sample_rate, library_name


def synthesize_calls(specs):
    """Retrieve signed 16-bit mono PCM; this build-only API is single-threaded."""
    library, _callback, chunks, sample_rate, library_name = _speech_engine()
    calls = []
    for spec in specs:
        chunks.clear()
        text = ctypes.create_string_buffer(spec["ssml"].encode("utf-8"))
        # POS_CHARACTER=1; CHARS_UTF8 | SSML = 0x11; size includes NUL.
        result = library.espeak_Synth(text, len(text), 0, 1, 0, 0x11, None, None)
        if result != 0:
            raise RuntimeError(f"Synthesis failed for {spec['display_text']}: {result}")
        pcm = b"".join(chunks)
        if not pcm:
            raise RuntimeError(f"No audio retrieved for {spec['display_text']}")
        if sys.byteorder != "little":
            samples = array("h", pcm)
            samples.byteswap()
            pcm = samples.tobytes()
        calls.append({"number": spec["number"], "pcm": pcm})
    return {
        "sample_rate": sample_rate,
        "calls": calls,
        "espeak_version": library.espeak_Info(None).decode("utf-8"),
        "library": library_name,
    }


def encode_mp3(pcm, sample_rate, ffmpeg):
    """Encode raw little-endian mono speech into a small portable MP3."""
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
         "-f", "s16le", "-ar", str(sample_rate), "-ac", "1", "-i", "pipe:0",
         "-map_metadata", "-1", "-codec:a", "libmp3lame", "-b:a", "48k",
         "-ar", "22050", "-ac", "1", "-write_xing", "0", "-id3v2_version", "0",
         "-f", "mp3", "pipe:1"],
        input=pcm, capture_output=True, check=True,
    )
    return result.stdout


def generate_assets(output, ffmpeg):
    """Create a complete new bundle, including per-clip text and provenance."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    specs = [call_spec(number) for number in range(1, 76)]
    synthesis = synthesize_calls(specs)
    clips = []
    for spec, call in zip(specs, synthesis["calls"], strict=True):
        encoded = encode_mp3(call["pcm"], synthesis["sample_rate"], ffmpeg)
        (output / spec["file"]).write_bytes(encoded)
        clips.append({
            **spec,
            "bytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "source_pcm_sha256": hashlib.sha256(call["pcm"]).hexdigest(),
            "source_duration_seconds": round(len(call["pcm"]) / 2 / synthesis["sample_rate"], 6),
        })
    metadata = {
        "schema_version": 1,
        "language": "en-US",
        "voice": {
            "engine": "eSpeak NG", "name": "en-us", "rate_wpm": 145,
            "pitch": 50, "volume": 100, "letter_number_gap_ms": 200,
            "letter_pronunciations": {"B": "bee", "I": "eye", "N": "en", "G": "gee", "O": "oh"},
        },
        "encoding": {"format": "mp3", "encoder": "libmp3lame", "channels": 1,
                     "sample_rate_hz": 22050, "bitrate_bps": 48000},
        "generation": {
            "library": synthesis["library"],
            "espeak_version": synthesis["espeak_version"],
            "ffmpeg_version": subprocess.run(
                [ffmpeg, "-version"], capture_output=True, text=True, check=True
            ).stdout.splitlines()[0],
            "python_version": sys.version,
            "method": "ctypes synchronous PCM retrieval callback; local ffmpeg conversion",
            "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "invocation": sys.argv,
            "regenerate_from_frontend": ["python3", "scripts/generate-callouts.py", "--ffmpeg", "ffmpeg"],
            "network_used": False,
        },
        "runtime": {"services": [], "network_required": False,
                    "speech_engine_required": False, "installed_voice_required": False,
                    "playback": "Browser-native MP3 playback of bundled same-origin files"},
        "total_audio_bytes": sum(clip["bytes"] for clip in clips),
        "clips": clips,
    }
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    (output / "README.md").write_text(
        """# Bundled English Bingo calls

`1.mp3` through `75.mp3` are real locally synthesized English (US) letter +
number calls: B 1–15, I 16–30, N 31–45, G 46–60, O 61–75.

## Playback / offline use

No runtime speech service, cloud API, speech-synthesis engine, installed voice,
Python, eSpeak or ffmpeg is required. These are ordinary bundled mono MP3 files
served by the same app host. No internet connection is needed; the app server/LAN
must still be reachable, or the files must already be available locally. This
bundle does not add a service worker or make an unvisited web page work offline.
Browser playback remains subject to its user-gesture/autoplay and volume rules.

## Voice and provenance

Generated from the existing local `libespeak-ng.so.1` using Python `ctypes`,
`AUDIO_OUTPUT_SYNCHRONOUS` and an in-memory signed 16-bit PCM retrieval callback;
no speaker/audio device, subprocess speech service or network call was used.
Voice `en-us`, 145 words/minute, pitch 50, volume 100. Explicit spoken letter
names (`bee`, `eye`, `en`, `gee`, `oh`) avoid ambiguous single-character parsing.
An SSML 200 ms break separates each letter and number, with eSpeak's natural
word/sentence spacing. ffmpeg/libmp3lame encodes 22,050 Hz, 48 kbit/s mono MP3;
ID3 tags and Xing headers are omitted. No runtime dependency on installed voices.

`metadata.json` records exact display text, spoken text and SSML for every number,
source PCM duration and SHA-256, delivered MP3 bytes and SHA-256, synthesis and
encoder versions, generator source hash, actual invocation and total audio bytes.
These hashes bind the files to the synthesis inputs; they are not transcription
or human listening certification. The synthetic eSpeak voice is intelligible but
robotic, not a recording of a human caller. Local tool/voice versions and eSpeak's
process-global synthesis state may change regenerated bytes; revalidate them.

## Regenerate / validate (development only)

Use only already-installed Python 3, libespeak-ng with local `en-us` data and
ffmpeg with `libmp3lame`. Nothing is installed or fetched by these scripts.
From `source/frontend`, generate into a **new** directory (existing directories
are refused, never silently overwritten):

```sh
python3 scripts/generate-callouts.py --ffmpeg /path/to/ffmpeg --output /path/to/new-calls
FFMPEG=/path/to/ffmpeg python3 scripts/test_callout_assets.py
```

The generator's default destination is `public/audio/calls`. Validate just the
bundled files with `BundledAssetTests` appended to the test command; that mode
needs ffmpeg but no installed eSpeak voice. The full suite also exercises local
generation. Set `TMPDIR` to your development scratch directory for tests.
""",
        encoding="utf-8",
    )
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "public/audio/calls")
    parser.add_argument("--ffmpeg", default=os.environ.get("FFMPEG") or shutil.which("ffmpeg") or "ffmpeg")
    arguments = parser.parse_args()
    try:
        metadata = generate_assets(arguments.output, arguments.ffmpeg)
    except FileExistsError:
        parser.error(f"Output already exists; refusing to overwrite: {arguments.output}")
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Generation failed: {error}\n")
    print(json.dumps({"output": str(arguments.output), "clips": len(metadata["clips"]),
                      "total_audio_bytes": metadata["total_audio_bytes"]}))


if __name__ == "__main__":
    main()
