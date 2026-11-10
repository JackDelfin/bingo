#!/usr/bin/env python3
"""Offline regression tests for bundled Bingo calls; Python standard library only."""

from array import array
import importlib.util
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT_DIR = Path(__file__).resolve().parent
FFMPEG = os.environ.get("FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"
TEST_TMPDIR = os.environ.get("CALLOUT_TEST_TMPDIR")
module_spec = importlib.util.spec_from_file_location(
    "generate_callouts", SCRIPT_DIR / "generate-callouts.py"
)
if module_spec is None or module_spec.loader is None:
    raise RuntimeError("Cannot load the local callout generator")
generator = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(generator)


def validate_assets(directory, ffmpeg=FFMPEG):
    """Validate bundle integrity without relying on any installed speech voice."""
    check = unittest.TestCase()
    directory = Path(directory)
    check.assertTrue(directory.is_dir(), f"Missing bundle: {directory}")
    check.assertEqual(
        {p.name for p in directory.iterdir()},
        {f"{n}.mp3" for n in range(1, 76)} | {"metadata.json", "README.md"},
        "Bundle must have exactly the 75 numbered MP3 files plus metadata and README",
    )
    check.assertTrue(all(p.is_file() and not p.is_symlink() for p in directory.iterdir()))
    metadata = json.loads((directory / "metadata.json").read_text())
    check.assertEqual(metadata["schema_version"], 1)
    check.assertEqual(len(metadata["clips"]), 75)
    check.assertEqual(metadata["language"], "en-US")
    check.assertEqual(metadata["voice"]["name"], "en-us")
    check.assertEqual(metadata["voice"]["rate_wpm"], 145)
    check.assertEqual(metadata["voice"]["letter_number_gap_ms"], 200)
    check.assertEqual(metadata["encoding"], {
        "format": "mp3", "encoder": "libmp3lame", "channels": 1,
        "sample_rate_hz": 22050, "bitrate_bps": 48000,
    })
    check.assertEqual(metadata["runtime"]["services"], [])
    for field in ("network_required", "speech_engine_required", "installed_voice_required"):
        check.assertIs(metadata["runtime"][field], False)
    check.assertIs(metadata["generation"]["network_used"], False)
    check.assertTrue(metadata["generation"]["espeak_version"])
    check.assertTrue(metadata["generation"]["ffmpeg_version"])
    check.assertRegex(metadata["generation"]["generator_sha256"], "^[0-9a-f]{64}$")
    check.assertIn("No runtime", (directory / "README.md").read_text())
    units = (
        "zero one two three four five six seven eight nine ten eleven twelve "
        "thirteen fourteen fifteen sixteen seventeen eighteen nineteen"
    ).split()
    tens = {20: "twenty", 30: "thirty", 40: "forty", 50: "fifty", 60: "sixty", 70: "seventy"}
    spoken_letters = {"B": "bee", "I": "eye", "N": "en", "G": "gee", "O": "oh"}
    total_bytes = 0
    hashes = set()
    for number, clip in enumerate(metadata["clips"], start=1):
        letter = "BINGO"[(number - 1) // 15]
        words = units[number] if number < 20 else tens[number // 10 * 10]
        if number >= 20 and number % 10:
            words += "-" + units[number % 10]
        expected = {
            "number": number, "letter": letter, "file": f"{number}.mp3",
            "display_text": f"{letter} {number}",
            "spoken_text": f"{spoken_letters[letter]}, {words}",
            "ssml": f'<speak>{spoken_letters[letter]}<break time="200ms"/>{words}</speak>',
        }
        check.assertEqual({key: clip[key] for key in expected}, expected)
        content = (directory / f"{number}.mp3").read_bytes()
        check.assertGreater(len(content), 1000, f"Empty/tiny clip: {number}")
        check.assertLess(len(content), 30000, f"Oversized clip: {number}")
        check.assertEqual(clip["bytes"], len(content))
        check.assertEqual(clip["sha256"], hashlib.sha256(content).hexdigest(), f"Hash mismatch: {number}")
        check.assertRegex(clip["source_pcm_sha256"], "^[0-9a-f]{64}$")
        check.assertGreater(clip["source_duration_seconds"], 0.6)
        check.assertLess(clip["source_duration_seconds"], 5.0)
        hashes.add(clip["sha256"])
        total_bytes += len(content)
    check.assertEqual(len(hashes), 75, "Every number must have its own distinct audio")
    check.assertEqual(metadata["total_audio_bytes"], total_bytes)
    durations = []
    rms_values = []
    active_fractions = []
    for clip in metadata["clips"]:
        filename = directory / clip["file"]
        decoded = subprocess.run(
            [ffmpeg, "-hide_banner", "-nostdin", "-xerror", "-err_detect", "explode",
             "-protocol_whitelist", "file,pipe", "-f", "mp3", "-i", str(filename),
             "-map", "0:a:0", "-f", "s16le", "-ac", "1", "-ar", "22050", "pipe:1"],
            capture_output=True, timeout=10,
        )
        check.assertEqual(decoded.returncode, 0, f"Undecodable {filename.name}: {decoded.stderr.decode(errors='replace')}")
        check.assertRegex(decoded.stderr, rb"Audio: mp3(?: \([^)]+\))?, 22050 Hz, mono")
        samples = array("h", decoded.stdout)
        if sys.byteorder != "little":
            samples.byteswap()
        duration = len(samples) / 22050
        check.assertGreater(duration, 0.6, f"Too short: {filename.name}")
        check.assertLess(duration, 5.0, f"Too long: {filename.name}")
        check.assertLess(abs(duration - clip["source_duration_seconds"]), 0.15,
                         f"Decoded/source duration mismatch: {filename.name}")
        rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
        active_fraction = sum(abs(sample) > 300 for sample in samples) / len(samples)
        check.assertGreater(rms, 200, f"Silent or near-silent: {filename.name}")
        check.assertGreater(active_fraction, 0.05, f"Insufficient speech signal: {filename.name}")
        check.assertGreater(max(abs(sample) for sample in samples), 1000)
        durations.append(duration)
        rms_values.append(rms)
        active_fractions.append(active_fraction)
    return {
        "clips": 75, "decoded_clips": len(durations), "total_audio_bytes": total_bytes,
        "duration_range_seconds": [round(min(durations), 6), round(max(durations), 6)],
        "total_duration_seconds": round(sum(durations), 6),
        "rms_range": [round(min(rms_values), 3), round(max(rms_values), 3)],
        "active_fraction_range": [round(min(active_fractions), 6), round(max(active_fractions), 6)],
    }


class ValidatorTests(unittest.TestCase):
    def test_audio_validation_rejects_silent_or_undecodable_hashed_files(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMPDIR) as temporary:
            output = Path(temporary) / "calls"
            generator.generate_assets(output, FFMPEG)
            original_metadata = (output / "metadata.json").read_bytes()
            original_audio = (output / "1.mp3").read_bytes()
            sample_count = round(json.loads(original_metadata)["clips"][0]["source_duration_seconds"] * 22050)
            silent = generator.encode_mp3(b"\0\0" * sample_count, 22050, FFMPEG)
            for label, bad_audio in (("silent", silent), ("undecodable", b"not mp3 audio" * 1000)):
                metadata = json.loads(original_metadata)
                clip = metadata["clips"][0]
                metadata["total_audio_bytes"] += len(bad_audio) - clip["bytes"]
                clip["bytes"] = len(bad_audio)
                clip["sha256"] = hashlib.sha256(bad_audio).hexdigest()
                (output / "1.mp3").write_bytes(bad_audio)
                (output / "metadata.json").write_text(json.dumps(metadata))
                with self.assertRaises(AssertionError, msg=label):
                    validate_assets(output)
            (output / "1.mp3").write_bytes(original_audio)
            (output / "metadata.json").write_bytes(original_metadata)
            report = validate_assets(output)
            self.assertEqual(report["decoded_clips"], 75)
            self.assertGreater(report["duration_range_seconds"][0], 0.6)
            self.assertLess(report["duration_range_seconds"][1], 5.0)
            self.assertGreater(report["rms_range"][0], 200)
            self.assertGreater(report["active_fraction_range"][0], 0.05)

    def test_integrity_validation_rejects_missing_extra_altered_or_mislabeled_assets(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMPDIR) as temporary:
            output = Path(temporary) / "calls"
            generator.generate_assets(output, FFMPEG)
            report = validate_assets(output)
            self.assertIsInstance(report, dict)
            self.assertEqual(report["clips"], 75)
            original_metadata = (output / "metadata.json").read_bytes()
            original_clip = (output / "1.mp3").read_bytes()

            (output / "1.mp3").unlink()
            with self.assertRaises(AssertionError):
                validate_assets(output)
            (output / "1.mp3").write_bytes(original_clip)

            (output / "76.mp3").write_bytes(original_clip)
            with self.assertRaises(AssertionError):
                validate_assets(output)
            (output / "76.mp3").unlink()

            (output / "1.mp3").write_bytes(original_clip + b"tampered")
            with self.assertRaises(AssertionError):
                validate_assets(output)
            (output / "1.mp3").write_bytes(original_clip)

            for field, wrong in (("number", 2), ("letter", "I"), ("display_text", "I 1"),
                                 ("spoken_text", "eye, one"), ("ssml", "<speak>two</speak>"),
                                 ("file", "../1.mp3")):
                metadata = json.loads(original_metadata)
                metadata["clips"][0][field] = wrong
                (output / "metadata.json").write_text(json.dumps(metadata))
                with self.subTest(field=field), self.assertRaises(AssertionError):
                    validate_assets(output)
            (output / "metadata.json").write_bytes(original_metadata)
            self.assertEqual(validate_assets(output)["clips"], 75)


class BundledAssetTests(unittest.TestCase):
    def test_all_75_shipped_calls_are_decodable_hashed_speech(self):
        directory = SCRIPT_DIR.parent / "public/audio/calls"
        report = validate_assets(directory)
        metadata = json.loads((directory / "metadata.json").read_text())
        source_hash = hashlib.sha256((SCRIPT_DIR / "generate-callouts.py").read_bytes()).hexdigest()
        self.assertEqual(metadata["generation"]["generator_sha256"], source_hash,
                         "Bundled provenance must refer to the shipped generator")
        print(json.dumps({"bundled_assets": report}, sort_keys=True))


class CallTextTests(unittest.TestCase):
    def test_invalid_numbers_are_rejected_with_value_error(self):
        for invalid in (0, -1, 76, True, 1.0, "1", None):
            with self.subTest(invalid=invalid):
                error_type = None
                try:
                    generator.call_spec(invalid)
                except Exception as error:
                    error_type = type(error)
                self.assertIs(error_type, ValueError)

    def test_all_75_calls_have_correct_letter_and_unambiguous_spoken_text(self):
        units = (
            "zero one two three four five six seven eight nine ten eleven twelve "
            "thirteen fourteen fifteen sixteen seventeen eighteen nineteen"
        ).split()
        tens = {20: "twenty", 30: "thirty", 40: "forty", 50: "fifty", 60: "sixty", 70: "seventy"}
        spoken_letters = {"B": "bee", "I": "eye", "N": "en", "G": "gee", "O": "oh"}
        for number in range(1, 76):
            with self.subTest(number=number):
                letter = "BINGO"[(number - 1) // 15]
                words = units[number] if number < 20 else tens[number // 10 * 10]
                if number >= 20 and number % 10:
                    words += "-" + units[number % 10]
                self.assertEqual(
                    generator.call_spec(number),
                    {
                        "number": number,
                        "letter": letter,
                        "display_text": f"{letter} {number}",
                        "spoken_text": f"{spoken_letters[letter]}, {words}",
                        "ssml": f'<speak>{spoken_letters[letter]}<break time="200ms"/>{words}</speak>',
                        "file": f"{number}.mp3",
                    },
                )


class BundleGenerationTests(unittest.TestCase):
    def test_command_line_generates_a_bundle_and_refuses_existing_output(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMPDIR) as temporary:
            output = Path(temporary) / "calls"
            command = [sys.executable, str(SCRIPT_DIR / "generate-callouts.py"),
                       "--ffmpeg", FFMPEG, "--output", str(output)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((output / "75.mp3").is_file(), "CLI must actually generate audio")
            self.assertEqual(json.loads(result.stdout)["clips"], 75)
            before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()}
            repeated = subprocess.run(command, capture_output=True, text=True, timeout=15)
            self.assertNotEqual(repeated.returncode, 0)
            self.assertIn("already exists", repeated.stderr)
            self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()})

    def test_generation_writes_exactly_75_hashed_calls_with_offline_provenance(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMPDIR) as temporary:
            output = Path(temporary) / "calls"
            generator.generate_assets(output, FFMPEG)
            self.assertTrue(output.is_dir(), "Generation must produce the bundle")
            self.assertEqual(
                {p.name for p in output.iterdir()},
                {f"{n}.mp3" for n in range(1, 76)} | {"metadata.json", "README.md"},
            )
            metadata = json.loads((output / "metadata.json").read_text())
            self.assertEqual(metadata["schema_version"], 1)
            self.assertEqual(metadata["voice"]["name"], "en-us")
            self.assertEqual(metadata["voice"]["rate_wpm"], 145)
            self.assertEqual(metadata["voice"]["letter_number_gap_ms"], 200)
            self.assertEqual(metadata["runtime"]["services"], [])
            for field in ("network_required", "speech_engine_required", "installed_voice_required"):
                self.assertIs(metadata["runtime"][field], False)
            self.assertTrue(metadata["generation"]["espeak_version"])
            self.assertTrue(metadata["generation"]["ffmpeg_version"])
            self.assertEqual(len(metadata["clips"]), 75)
            self.assertEqual(metadata["total_audio_bytes"], sum(p.stat().st_size for p in output.glob("*.mp3")))
            for number, clip in enumerate(metadata["clips"], start=1):
                with self.subTest(number=number):
                    expected = generator.call_spec(number)
                    self.assertEqual({key: clip[key] for key in expected}, expected)
                    content = (output / clip["file"]).read_bytes()
                    self.assertEqual(clip["bytes"], len(content))
                    self.assertEqual(clip["sha256"], hashlib.sha256(content).hexdigest())
                    self.assertGreater(clip["source_duration_seconds"], 0.6)
                    self.assertRegex(clip["source_pcm_sha256"], "^[0-9a-f]{64}$")
            self.assertIn("No runtime", (output / "README.md").read_text())


class EncodingTests(unittest.TestCase):
    def test_real_retrieved_speech_round_trips_through_small_mono_mp3(self):
        self.assertIsNotNone(FFMPEG, "Set FFMPEG to an existing local binary")
        result = generator.synthesize_calls([generator.call_spec(75)])
        encoded = generator.encode_mp3(result["calls"][0]["pcm"], result["sample_rate"], FFMPEG)
        self.assertGreater(len(encoded), 1000)
        self.assertLess(len(encoded), 30000)
        decoded = subprocess.run(
            [FFMPEG, "-hide_banner", "-xerror", "-err_detect", "explode", "-i", "pipe:0",
             "-f", "s16le", "-ac", "1", "-ar", "22050", "pipe:1"],
            input=encoded, capture_output=True, check=True,
        )
        self.assertRegex(decoded.stderr, rb"Audio: mp3(?: \([^)]+\))?, 22050 Hz, mono")
        samples = array("h", decoded.stdout)
        self.assertGreater(len(samples) / 22050, 0.6)
        self.assertLess(len(samples) / 22050, 5.0)
        self.assertGreater(math.sqrt(sum(s * s for s in samples) / len(samples)), 200)


class SynthesisTests(unittest.TestCase):
    def test_repeated_batches_do_not_reinitialize_the_global_native_engine(self):
        code = (
            "import runpy; g = runpy.run_path(" + repr(str(SCRIPT_DIR / "generate-callouts.py")) + "); "
            "a = g['synthesize_calls']([g['call_spec'](1)]); "
            "b = g['synthesize_calls']([g['call_spec'](75)]); "
            "print(len(a['calls']), len(b['calls']))"
        )
        try:
            result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=5)
            observation = (result.returncode, result.stdout.strip())
        except subprocess.TimeoutExpired:
            observation = "native engine hung when reused"
        self.assertEqual(observation, (0, "1 1"))

    def test_local_us_voice_retrieves_nonempty_speech_for_each_letter(self):
        numbers = (1, 16, 31, 46, 61)
        result = generator.synthesize_calls([generator.call_spec(n) for n in numbers])
        self.assertEqual(len(result["calls"]), len(numbers))
        self.assertEqual(result["sample_rate"], 22050)
        self.assertTrue(result["espeak_version"])
        for expected, call in zip(numbers, result["calls"], strict=True):
            with self.subTest(number=expected):
                self.assertEqual(call["number"], expected)
                samples = array("h", call["pcm"])
                duration = len(samples) / result["sample_rate"]
                self.assertGreater(duration, 0.6)
                self.assertLess(duration, 5.0)
                self.assertGreater(math.sqrt(sum(s * s for s in samples) / len(samples)), 200)
                self.assertGreater(sum(abs(s) > 300 for s in samples) / len(samples), 0.05)


if __name__ == "__main__":
    unittest.main(verbosity=2)
