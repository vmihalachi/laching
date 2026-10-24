#!/usr/bin/env python3
"""A companion's recorded voices: renders every line once with ElevenLabs and ships the clips in the app.

The character never says free text: each line is a catalog string (`phrase.<id>`, or `phrase.<id>.1`, `.2`, … for the
parts of a joke), so it's recorded at dev time and played back from the bundle. The app makes no network requests; only
this script talks to ElevenLabs, sending nothing but the lines' text. Each voiced language has the app's voices
(`voice.voices` in companion.json, e.g. `["male", "female"]` or `["clown"]`: stable values the app's voice setting
stores). Names and paths come from the app's `companion.json` (`voice`, `l10n.catalog`, `conformance.dir`, `character`).
Run from anywhere inside the app:

    python3 laching/tools/voice.py status [--json]        # the pending report (offline)
    python3 laching/tools/voice.py check                  # exit 1 only on what would hurt users (offline):
                                                           # broken manifest, orphans, required clips of a shipping language
  Finding a voice (writes listening pages under <mac.dir>/build/voice/design/<lang>/<voice>/):
    python3 laching/tools/voice.py design --lang it --voice female [--description "…"]   # Voice Design candidates
    python3 laching/tools/voice.py library --lang it --voice female [--search "…"] [--sample 4]   # Voice Library
    python3 laching/tools/voice.py sample <voice_id> --lang it --voice female --name NAME   # any voice, same lines
    python3 laching/tools/voice.py adopt <generated_voice_id> --lang it --voice female    # keep a design candidate
    python3 laching/tools/voice.py use <voice_id> --lang it --voice female --name NAME    # or use an existing voice
  Recording:
    python3 laching/tools/voice.py render --lang it [--voice male] [--only id,id] [--force] [--dry-run] [--yes] [--prune]
    python3 laching/tools/voice.py retake <id>[,<id>…] --lang it [--voice male] --yes
    python3 laching/tools/voice.py review [--lang it]   # the review pages: listen through, flag, copy notes for Claude
    python3 laching/tools/voice.py site     # the friends' pages in the voices repository (render and retake refresh them)
    python3 laching/tools/voice.py publish -m "…"   # check, commit and push the voices repository, stage its pointer here

Files:
- `voice.config` (hand-edited): the model and, per language, its `status` (`testing` plays in Debug builds only,
  `shipping` in Release too), the `default` voice, text replacements, and per voice its ElevenLabs id, description,
  settings and take numbers.
- `voice.direction` (hand-edited): per clip id, delivery `tags` such as `[whispers]` (they go to the voice only when the
  model takes audio tags, and never show in the bubble) and `gain_db` against the loudness target.
- `<voice.dir>/Voice/<lang>/<voice>/<clip id>.m4a` and `Voice/manifest.json` (generated; the voices submodule).

Clip ids: `<id>` for a line, `<id>.1`, `<id>.2`, … for a joke's parts (each part its own clip, with its own `textHash`),
plus `voice.extraClips` (UI strings the character says). A phrase listed for some languages (`languages` in
phrases.json) is recorded in those only. A clip is *voiced* in a language and voice when it exists and the manifest's
`textHash` (sha256 of the exact bubble text) matches the catalog's current text. Otherwise it's *pending* there:
*missing* or *stale*; a joke is pending while any part is. The app never picks a phrase that's pending in the voice it
uses (unless it falls back to the system voice). Calls to ElevenLabs need the API key in the login Keychain (service
`voice.keychainService`) or `ELEVENLABS_API_KEY`; rendering also needs ffmpeg and afconvert.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import companion  # noqa: E402
from companion import base_language, fail  # noqa: E402

API = "https://api.elevenlabs.io"
FORMAT = 1
MOUTH_FPS = 12.5  # one mouth level per 80 ms
STATUSES = ("testing", "shipping")
REVIEW_TEMPLATE = Path(__file__).resolve().with_name("voice-review.html")
LANGUAGE_NAMES = {
    "ar": "Arabic", "ca": "Catalan", "cs": "Czech", "da": "Danish", "de": "German", "el": "Greek", "en": "English",
    "es": "Spanish", "fi": "Finnish", "fr": "French", "he": "Hebrew", "hi": "Hindi", "hu": "Hungarian",
    "id": "Indonesian", "it": "Italian", "ja": "Japanese", "ko": "Korean", "nb": "Norwegian", "nl": "Dutch",
    "pl": "Polish", "pt": "Portuguese", "pt-BR": "Portuguese", "pt-PT": "Portuguese (Portugal)", "ro": "Romanian",
    "ru": "Russian", "sk": "Slovak", "sv": "Swedish", "th": "Thai", "tr": "Turkish", "uk": "Ukrainian",
    "vi": "Vietnamese", "zh-Hans": "Chinese (Simplified)", "zh-Hant": "Chinese (Traditional)",
}

# Set by `configure` from the app's companion.json (tests point them elsewhere).
APP: companion.Config
ROOT: Path
CONFIG: Path
DIRECTION: Path
CATALOG: Path
PHRASES: Path
VOICE: Path
MANIFEST: Path
WORK: Path
KINDS: tuple[str, ...] = ()
KEYCHAIN_SERVICE = ""
CHARACTER = ""
REVIEW_TITLE = ""
SITE_NOTICE = ""


def configure(config: companion.Config | None = None) -> None:
    """Points the module at an app: its voice files, catalog, bank and names."""
    global APP, ROOT, CONFIG, DIRECTION, CATALOG, PHRASES, VOICE, MANIFEST, WORK, KINDS, KEYCHAIN_SERVICE
    global CHARACTER, REVIEW_TITLE, SITE_NOTICE
    APP = config or companion.load()
    ROOT = APP.root
    CONFIG = APP.require_path("voice.config")
    DIRECTION = APP.path("voice.direction") or CONFIG.with_name("voice-direction.json")
    CATALOG = APP.require_path("l10n.catalog")
    PHRASES = APP.require_phrases_file()
    VOICE = APP.require_path("voice.dir") / "Voice"
    MANIFEST = VOICE / "manifest.json"
    WORK = APP.work_dir / "voice"
    KINDS = tuple(APP.require("voice.voices"))
    KEYCHAIN_SERVICE = APP.get("voice.keychainService") or f"{APP.require('name')}-elevenlabs"
    CHARACTER = APP.get("character") or APP.require("displayName")
    REVIEW_TITLE = APP.get("voice.reviewTitle") or f"{CHARACTER}'s voice"
    rights = APP.get("voice.siteCopyright") or f"The recordings and texts are © {APP.require('displayName')}, all rights reserved"
    SITE_NOTICE = (
        f"Shared with friends who help review {CHARACTER}'s voice. {rights.rstrip('.')}. "
        "Please don't reuse, redistribute or train on them."
    )


def rel(path: Path) -> str:
    return companion.rel(path, ROOT)


# ---------------------------------------------------------------------------------------------- inputs


def load_json(path: Path, default=None):
    if not path.exists():
        if default is not None:
            return default
        fail(f"{rel(path)} is missing")
    return json.loads(path.read_text("utf8"))


def dump_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", "utf8")


def load_config() -> dict:
    config = load_json(CONFIG)
    for lang, entry in config.get("languages", {}).items():
        if entry.get("status") not in STATUSES:
            fail(f"voice-config.json: languages.{lang}.status must be one of {', '.join(STATUSES)}")
        if entry.get("default") not in KINDS:
            fail(f"voice-config.json: languages.{lang}.default must be one of {', '.join(KINDS)}")
        for kind in entry.get("voices", {}):
            if kind not in KINDS:
                fail(f"voice-config.json: languages.{lang}.voices.{kind}: voices are {', '.join(KINDS)}")
    return config


def language(config: dict, lang: str) -> dict:
    return config.get("languages", {}).get(lang) or fail(f"{lang} isn't in voice-config.json languages")


def voice_of(config: dict, lang: str, kind: str | None) -> tuple[str, dict]:
    entry = language(config, lang)
    kind = kind or entry["default"]
    return kind, entry.setdefault("voices", {}).setdefault(kind, {})


def ready_voices(config: dict, lang: str) -> list[str]:
    """The language's voices that have an ElevenLabs id, default first."""
    entry = language(config, lang)
    kinds = [k for k in KINDS if entry.get("voices", {}).get(k, {}).get("voice_id")]
    return sorted(kinds, key=lambda k: k != entry["default"])


def bank() -> list[dict]:
    """The spoken phrases (lines and jokes; tunes are sounds), in bank order."""
    if not PHRASES.exists():
        fail(f"{rel(PHRASES)} is missing: regenerate the vectors first (laching/tools/conformance.sh)")
    return [p for p in json.loads(PHRASES.read_text("utf8"))["phrases"] if p["kind"] in ("line", "sequence")]


def phrase_clips(phrase: dict) -> dict[str, str]:
    """A phrase's clip ids → catalog keys: `<id>` → `phrase.<id>`, or `<id>.<n>` → its part's key."""
    if phrase["kind"] == "sequence":
        return {f"{phrase['id']}.{n}": key for n, key in enumerate(phrase["keys"], start=1)}
    return {phrase["id"]: phrase.get("key") or f"phrase.{phrase['id']}"}


def speaks(phrase: dict, lang: str | None) -> bool:
    """Whether the phrase exists in `lang` (every phrase when None)."""
    languages = phrase.get("languages")
    return lang is None or not languages or base_language(lang) in languages


def extra_clips(config: dict) -> dict[str, str]:
    """UI strings the character says: clip id → catalog key (its source text). companion.json, then voice-config."""
    extra = dict(APP.get("voice.extraClips", {}))
    extra.update(config.get("extra", {}))
    return extra


def required_clips(config: dict) -> list[str]:
    return list(dict.fromkeys([*APP.get("voice.required", []), *config.get("required", [])]))


def clip_keys(config: dict, lang: str | None = None) -> dict[str, str]:
    """Clip id → catalog key, for every phrase in `lang` (all when None) plus the extra clips, in a stable order."""
    keys: dict[str, str] = {}
    for phrase in bank():
        if speaks(phrase, lang):
            keys.update(phrase_clips(phrase))
    keys.update(extra_clips(config))
    return keys


def clip_phrases() -> dict[str, tuple[dict, int, int]]:
    """Clip id → (its phrase, part number from 1, part count) for every phrase clip."""
    out = {}
    for phrase in bank():
        clips = list(phrase_clips(phrase))
        for n, clip in enumerate(clips, start=1):
            out[clip] = (phrase, n, len(clips))
    return out


def catalog_texts(lang: str, keys: dict[str, str]) -> dict[str, str]:
    """The exact text the app shows for each clip id in `lang` (only ids that have a translation)."""
    data = json.loads(CATALOG.read_text("utf8"))
    source = data.get("sourceLanguage", "en")
    out = {}
    for clip, key in keys.items():
        entry = data["strings"].get(key)
        if entry is None:
            continue
        unit = entry.get("localizations", {}).get(lang, {}).get("stringUnit")
        if unit and unit.get("value"):
            out[clip] = unit["value"]
        elif lang == source and not entry.get("localizations"):
            out[clip] = key  # an extracted English string with no explicit localization: the key is the text
    return out


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf8")).hexdigest()


def clip_path(lang: str, kind: str, clip: str) -> Path:
    return VOICE / lang / kind / f"{clip}.m4a"


# ---------------------------------------------------------------------------------------------- what to say


def spoken_text(text: str, clip: str, lang: str, config: dict, direction: dict) -> str:
    """What the voice gets: the bubble text with pronunciation replacements and, if the model takes them, tags."""
    spoken = text
    for old, new in language(config, lang).get("replace", {}).items():
        spoken = spoken.replace(old, new)
    tags = direction.get(clip, {}).get("tags", "")
    if tags and config.get("audio_tags", False):
        spoken = f"{tags} {spoken}"
    return spoken


def seed_for(clip: str, lang: str, kind: str, voice: dict, config: dict) -> int:
    take = voice.get("takes", {}).get(clip, 1)
    digest = hashlib.sha256(f"{config.get('seed', 0)}:{lang}:{kind}:{clip}:{take}".encode()).digest()
    return int.from_bytes(digest[:4], "big")


def request_for(clip: str, text: str, lang: str, kind: str, config: dict, direction: dict) -> dict:
    _, voice = voice_of(config, lang, kind)
    return {
        "text": spoken_text(text, clip, lang, config, direction),
        "model_id": voice.get("model", config["model"]),
        "language_code": lang.split("-")[0],
        "voice_settings": voice.get("settings", config.get("settings", {})),
        "seed": seed_for(clip, lang, kind, voice, config),
    }


def loudness_for(clip: str, config: dict, direction: dict) -> float:
    """The clip's loudness target: the config's, plus the direction's `gain_db` (whispers sit a little lower)."""
    return config.get("loudness_lufs", -19) + direction.get(clip, {}).get("gain_db", 0)


def render_hash(voice_id: str, request: dict, lufs: float) -> str:
    return text_hash(json.dumps({"voice": voice_id, "lufs": lufs, **request}, sort_keys=True, ensure_ascii=False))


# ---------------------------------------------------------------------------------------------- the report


def load_manifest() -> dict:
    return load_json(MANIFEST, {"format": FORMAT, "mouthFPS": MOUTH_FPS, "languages": {}})


def manifest_clips(manifest: dict, lang: str, kind: str) -> dict:
    return manifest.get("languages", {}).get(lang, {}).get("voices", {}).get(kind, {}).get("clips", {})


def pending(lang: str, kind: str, config: dict, manifest: dict) -> dict:
    """Per clip id: None when voiced, else "missing", "stale" or "untranslated"."""
    keys = clip_keys(config, lang)
    texts = catalog_texts(lang, keys)
    clips = manifest_clips(manifest, lang, kind)
    state = {}
    for clip in keys:
        if clip not in texts:
            state[clip] = "untranslated"
        elif clip not in clips or not clip_path(lang, kind, clip).exists():
            state[clip] = "missing"
        elif clips[clip]["textHash"] != text_hash(texts[clip]):
            state[clip] = "stale"
        else:
            state[clip] = None
    return state


def gaps(state: dict) -> list[str]:
    """Tags and flavors left without a single voiced phrase: the character would fall back or stay silent there. A joke
    is voiced when every part is."""
    lines = [p for p in bank() if all(clip in state for clip in phrase_clips(p))]
    voiced = [p for p in lines if all(state[clip] is None for clip in phrase_clips(p))]
    out = [f"tag {t}" for t in sorted({t for p in lines for t in p["tags"]})
           if not any(t in p["tags"] for p in voiced)]
    out += [f"flavor {f}" for f in sorted({p.get("flavor") for p in lines if p.get("flavor")})
            if not any(p.get("flavor") == f for p in voiced)]
    return out


def report(config: dict) -> dict:
    manifest = load_manifest()
    languages = config.get("languages", {})
    data = json.loads(CATALOG.read_text("utf8"))
    all_langs = sorted({lang for e in data["strings"].values() for lang in e.get("localizations", {})})
    out = {"voiced": {}, "notVoiced": [lang for lang in all_langs if lang not in languages]}
    for lang, entry in sorted(languages.items()):
        voices = {}
        for kind in KINDS:
            voice = entry.get("voices", {}).get(kind, {})
            if not voice.get("voice_id"):
                voices[kind] = {"name": voice.get("name", ""), "ready": False}
                continue
            state = pending(lang, kind, config, manifest)
            voices[kind] = {
                "name": voice.get("name", ""),
                "ready": True,
                "voiced": sorted(c for c, s in state.items() if s is None),
                "missing": [c for c, s in state.items() if s == "missing"],
                "stale": [c for c, s in state.items() if s == "stale"],
                "untranslated": [c for c, s in state.items() if s == "untranslated"],
                "requiredPending": [c for c in required_clips(config) if state.get(c) is not None],
                "gaps": gaps(state),
                "total": len(state),
            }
        out["voiced"][lang] = {"status": entry["status"], "default": entry["default"], "voices": voices}
    return out


def summary_line(rep: dict) -> str:
    parts = []
    for lang, r in rep["voiced"].items():
        bits = []
        for kind, v in r["voices"].items():
            if not v["ready"]:
                bits.append(f"{kind} not set up")
                continue
            waiting = len(v["missing"]) + len(v["stale"]) + len(v["untranslated"])
            bits.append(f"{kind} {v['name']} {len(v['voiced'])}/{v['total']} voiced, {waiting} pending")
        parts.append(f"{lang} ({r['status']}): " + ", ".join(bits))
    if rep["notVoiced"]:
        parts.append("not voiced: " + ", ".join(rep["notVoiced"]))
    return "voice: " + "; ".join(parts) if parts else "voice: no voiced languages"


def cmd_status(args) -> int:
    rep = report(load_config())
    if args.json:
        print(json.dumps(rep, indent=2))
        return 0
    print(summary_line(rep))
    for lang, r in rep["voiced"].items():
        for kind, v in r["voices"].items():
            for label in ("missing", "stale", "untranslated", "requiredPending", "gaps"):
                if v.get(label):
                    print(f"  {lang}/{kind} {label} ({len(v[label])}): {', '.join(v[label])}")
    return 0


def cmd_check(args) -> int:
    config = load_config()
    if not VOICE.exists() and not (VOICE.parent / ".git").exists() and (ROOT / ".gitmodules").exists():
        fail(f"{rel(VOICE.parent)} isn't checked out: run `git submodule update --init`")
    manifest = load_manifest()
    problems = []
    if manifest.get("format") != FORMAT:
        problems.append(f"manifest format {manifest.get('format')} (expected {FORMAT})")
    for lang, entry in manifest.get("languages", {}).items():
        if lang not in config.get("languages", {}):
            problems.append(f"manifest has {lang}, which voice-config.json doesn't list")
            continue
        keys = clip_keys(config, lang)
        for field in ("status", "default"):
            if entry.get(field) != config["languages"][lang][field]:
                problems.append(f"{lang}: manifest {field} {entry.get(field)} ≠ voice-config.json; run render")
        for kind, voice in entry.get("voices", {}).items():
            for clip, meta in voice.get("clips", {}).items():
                if clip not in keys:
                    problems.append(f"{lang}/{kind}/{clip}: orphan (no such line); run render --prune")
                if not clip_path(lang, kind, clip).exists():
                    problems.append(f"{lang}/{kind}/{clip}.m4a: in the manifest but missing on disk")
                for field in ("textHash", "renderHash", "duration", "mouth"):
                    if field not in meta:
                        problems.append(f"{lang}/{kind}/{clip}: manifest entry lacks {field}")
    for path in VOICE.glob("*/*/*.m4a") if VOICE.exists() else []:
        lang, kind, clip = path.parent.parent.name, path.parent.name, path.stem
        if clip not in manifest_clips(manifest, lang, kind):
            problems.append(f"{rel(path)}: not in the manifest")
    if (VOICE.parent / ".git").exists():  # the voices repository (a submodule's .git is a file)
        problems += [f"{rel(p)}: the friends' site is stale; run voice.py site" for p in stale_site(config, manifest)]
    rep = report(config)
    for lang, r in rep["voiced"].items():
        for kind, v in r["voices"].items():
            has_clips = bool(v.get("voiced"))
            for clip in v.get("requiredPending", []):
                where = f"{lang}/{kind}/{clip}: required clip is pending"
                if r["status"] == "shipping" and (kind == r["default"] or has_clips):
                    problems.append(f"{where} in a shipping language")
                else:
                    print(f"voice: {where} (fine while {lang} is {r['status']})")
    for problem in problems:
        print(f"voice: {problem}", file=sys.stderr)
    print(summary_line(rep))
    return 1 if problems else 0


# ---------------------------------------------------------------------------------------------- ElevenLabs


def api_key() -> str:
    """ELEVENLABS_API_KEY, else the login Keychain item `voice.keychainService` (never printed, never committed)."""
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key and shutil.which("security"):
        found = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                               capture_output=True, text=True)
        key = found.stdout.strip() if found.returncode == 0 else ""
    if not key:
        fail(f'no API key: run `security add-generic-password -a "$USER" -s {KEYCHAIN_SERVICE} -w` '
             "and paste the key at the prompt, or set ELEVENLABS_API_KEY")
    return key


def call(method: str, path: str, body: dict | None = None, query: dict | None = None, expect_audio: bool = False):
    url = f"{API}{path}" + (f"?{urllib.parse.urlencode(query)}" if query else "")
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf8") if body is not None else None,
        headers={"xi-api-key": api_key(), "Content-Type": "application/json"},
        method=method,
    )
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                data = response.read()
                return data if expect_audio else json.loads(data)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf8", "replace")[:500]
            if error.code in (409, 429) or error.code >= 500:
                wait = 2**attempt
                print(f"  {error.code}, retrying in {wait}s: {detail}", file=sys.stderr)
                time.sleep(wait)
                continue
            fail(f"ElevenLabs {path}: HTTP {error.code}: {detail}")
    fail(f"ElevenLabs {path}: gave up after retries")


def tts(voice_id: str, request: dict, config: dict) -> bytes:
    return call("POST", f"/v1/text-to-speech/{voice_id}", request,
                {"output_format": config.get("output_format", "mp3_44100_128")}, expect_audio=True)


# ---------------------------------------------------------------------------------------------- audio


def tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        fail(f"{name} is required (brew install ffmpeg; afconvert ships with macOS)")
    return path


def run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        fail(f"{Path(cmd[0]).name} failed: {result.stderr[-800:]}")
    return result.stderr


TRIM = (
    "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.04,"
    "areverse,silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.08,areverse"
)


def process(source: Path, out_m4a: Path, lufs: float) -> tuple[float, str]:
    """Trims silence, normalizes loudness (two-pass, linear) and encodes mono AAC. Returns (duration, mouth)."""
    ffmpeg = tool("ffmpeg")
    with tempfile.TemporaryDirectory() as tmp:
        measure = run([ffmpeg, "-hide_banner", "-nostats", "-i", str(source), "-af",
                       f"{TRIM},loudnorm=I={lufs}:TP=-2:LRA=11:print_format=json", "-f", "null", "-"])
        stats = json.loads(measure[measure.rindex("{"):measure.rindex("}") + 1])
        norm = (f"loudnorm=I={lufs}:TP=-2:LRA=11:linear=true:measured_I={stats['input_i']}:"
                f"measured_TP={stats['input_tp']}:measured_LRA={stats['input_lra']}:"
                f"measured_thresh={stats['input_thresh']}:offset={stats['target_offset']}")
        wav = Path(tmp) / "clip.wav"
        run([ffmpeg, "-hide_banner", "-nostats", "-y", "-i", str(source), "-af", f"{TRIM},{norm}",
             "-ac", "1", "-ar", "44100", "-sample_fmt", "s16", str(wav)])
        duration, mouth = analyze(wav)
        out_m4a.parent.mkdir(parents=True, exist_ok=True)
        run([tool("afconvert"), "-f", "m4af", "-d", "aac", "-b", "48000", "-c", "1", "-q", "127",
             str(wav), str(out_m4a)])
    return duration, mouth


def analyze(wav_path: Path) -> tuple[float, str]:
    """The clip's duration and its mouth envelope: one digit 0–3 per 1/MOUTH_FPS s, from RMS loudness."""
    with wave.open(str(wav_path)) as w:
        rate, frames = w.getframerate(), w.readframes(w.getnframes())
    samples = array("h", frames)
    step = int(rate / MOUTH_FPS)
    levels = []
    for start in range(0, len(samples), step):
        chunk = samples[start:start + step]
        levels.append((sum(s * s for s in chunk) / max(len(chunk), 1)) ** 0.5)
    peak = max(levels, default=0) or 1
    mouth = "".join(str(min(3, int(4 * level / peak))) if level / peak > 0.08 else "0" for level in levels)
    return round(len(samples) / rate, 3), mouth


# ---------------------------------------------------------------------------------------------- recording


def direction_map() -> dict:
    return load_json(DIRECTION, {})


def plan_render(lang: str, kind: str, config: dict, only: set[str] | None, force: bool):
    """(clip, bubble text, request, renderHash, loudness) for every clip that needs rendering."""
    _, voice = voice_of(config, lang, kind)
    if not voice.get("voice_id"):
        fail(f"{lang}/{kind} has no voice yet: `voice.py design` + `adopt`, or `library`/`sample` + `use`")
    direction = direction_map()
    texts = catalog_texts(lang, clip_keys(config, lang))
    clips = manifest_clips(load_manifest(), lang, kind)
    todo = []
    for clip, text in texts.items():
        if only and clip not in only:
            continue
        request = request_for(clip, text, lang, kind, config, direction)
        lufs = loudness_for(clip, config, direction)
        rhash = render_hash(voice["voice_id"], request, lufs)
        current = clips.get(clip)
        if force or not current or not clip_path(lang, kind, clip).exists() or current.get("renderHash") != rhash:
            todo.append((clip, text, request, rhash, lufs))
    return todo


def cmd_render(args) -> int:
    config = load_config()
    kind, voice = voice_of(config, args.lang, args.voice)
    only = set(args.only.split(",")) if args.only else None
    todo = plan_render(args.lang, kind, config, only, args.force)
    characters = sum(len(r["text"]) for _, _, r, _, _ in todo)
    print(f"{args.lang}/{kind} ({voice.get('name', '')}): {len(todo)} clip(s) to render, "
          f"{characters} characters ≈ {characters} credits")
    manifest = load_manifest()
    manifest["format"], manifest["mouthFPS"] = FORMAT, MOUTH_FPS
    lang_entry = manifest.setdefault("languages", {}).setdefault(args.lang, {})
    lang_entry["status"] = config["languages"][args.lang]["status"]
    lang_entry["default"] = config["languages"][args.lang]["default"]
    entry = lang_entry.setdefault("voices", {}).setdefault(kind, {"clips": {}})
    entry["name"] = voice.get("name", "")
    if todo and not args.yes and not args.dry_run:  # before the prune: a refused run deletes nothing
        fail("add --yes to spend the credits above (check them with --dry-run first)")
    if args.prune:
        keys = clip_keys(config, args.lang)
        for clip in [c for c in entry["clips"] if c not in keys]:
            if args.dry_run:
                print(f"  would prune {clip}")
                continue
            clip_path(args.lang, kind, clip).unlink(missing_ok=True)
            del entry["clips"][clip]
            print(f"  pruned {clip}")
        if not args.dry_run:
            dump_json(MANIFEST, manifest)  # now, so a recording that stops (ElevenLabs, ^C) lists no deleted clip
    if args.dry_run:
        return 0
    for n, (clip, text, request, rhash, lufs) in enumerate(todo, 1):
        print(f"  [{n}/{len(todo)}] {clip}: {request['text']}")
        raw = WORK / "raw" / args.lang / kind / f"{clip}.mp3"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(tts(voice["voice_id"], request, config))
        duration, mouth = process(raw, clip_path(args.lang, kind, clip), lufs)
        entry["clips"][clip] = {"textHash": text_hash(text), "renderHash": rhash, "duration": duration, "mouth": mouth}
        dump_json(MANIFEST, manifest)  # after every clip, so an interrupted run keeps what it paid for
    dump_json(MANIFEST, manifest)
    if todo or args.prune:
        changed = sync_site(config, manifest)
        print(f"site: {len(changed)} file(s) updated for friends (voice.py publish ships them)")
    print(summary_line(report(config)))
    return 0


def cmd_retake(args) -> int:
    """New takes (new seeds) of one or more clips: `voice.py retake id1,id2 --lang it --voice male --yes`."""
    config = load_config()
    kind, voice = voice_of(config, args.lang, args.voice)
    takes = voice.setdefault("takes", {})
    ids = [i.strip() for i in args.id.split(",") if i.strip()]
    for clip in ids:
        takes[clip] = takes.get(clip, 1) + 1
        print(f"{args.lang}/{kind}/{clip}: take {takes[clip]}")
    if args.yes:
        dump_json(CONFIG, config)
    args.only, args.force, args.prune, args.dry_run = ",".join(ids), True, False, not args.yes
    return cmd_render(args)


# ---------------------------------------------------------------------------------------------- finding a voice


def sample_text(lang: str, config: dict) -> str:
    texts = catalog_texts(lang, clip_keys(config, lang))
    text = " ".join(texts[c] for c in config.get("design_lines", []) if c in texts)
    if len(text) < 100:
        fail(f"design_lines give {len(text)} characters in {lang}; Voice Design needs 100–1000")
    return text[:1000]


def design_dir(lang: str, kind: str) -> Path:
    return WORK / "design" / lang / kind


def design_index(lang: str, kind: str) -> Path:
    """Rebuilds the listening page from every round and sample so far, newest first."""
    out = design_dir(lang, kind)
    rows = []
    for meta_path in sorted(out.glob("*/design.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        meta = json.loads(meta_path.read_text("utf8"))
        for item in meta["items"]:
            rows.append((f"{meta_path.parent.name} · {item['label']}", f"{meta_path.parent.name}/{item['file']}",
                         f"{item['command']}  ·  {item.get('about') or meta['description']}"))
    page = out / "index.html"
    write_page(page, f"{CHARACTER}'s {kind} voice: {lang} candidates", "Each reads the same lines from the bank.", rows)
    return page


def next_dir(lang: str, kind: str, prefix: str) -> Path:
    n = 1 + sum(1 for _ in design_dir(lang, kind).glob(f"{prefix}-*"))
    path = design_dir(lang, kind) / f"{prefix}-{n}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cmd_design(args) -> int:
    config = load_config()
    kind, voice = voice_of(config, args.lang, args.voice)
    description = args.description or voice.get("description") or fail(f"{args.lang}/{kind}: pass --description")
    body = {"voice_description": description, "model_id": config.get("design_model", "eleven_ttv_v3"),
            "text": sample_text(args.lang, config)}
    if args.seed is not None:
        body["seed"] = args.seed
    print(f"designing {args.lang}/{kind} with: {description}")
    result = call("POST", "/v1/text-to-voice/design", body,
                  {"output_format": config.get("output_format", "mp3_44100_128")})
    out = next_dir(args.lang, kind, "round")
    items = []
    for n, preview in enumerate(result["previews"], 1):
        (out / f"candidate-{n}.mp3").write_bytes(base64.b64decode(preview["audio_base_64"]))
        gid = preview["generated_voice_id"]
        items.append({"label": f"Candidate {n}", "file": f"candidate-{n}.mp3", "generated_voice_id": gid,
                      "command": f"voice.py adopt {gid} --lang {args.lang} --voice {kind}"})
        print(f"  {out.name} candidate {n}: {gid}")
    dump_json(out / "design.json", {"description": description, "items": items})
    print(f"listen: {rel(design_index(args.lang, kind))}")
    return 0


def sample_voice(lang: str, kind: str, voice_id: str, name: str, about: str, config: dict) -> Path:
    _, voice = voice_of(config, lang, kind)
    request = {"text": sample_text(lang, config), "model_id": voice.get("model", config["model"]),
               "language_code": lang.split("-")[0], "voice_settings": voice.get("settings", config.get("settings", {}))}
    out = design_dir(lang, kind) / f"sample-{name}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "sample.mp3").write_bytes(tts(voice_id, request, config))
    dump_json(out / "design.json", {"description": about, "items": [
        {"label": name, "file": "sample.mp3", "about": about,
         "command": f"voice.py use {voice_id} --lang {lang} --voice {kind} --name {name}"}]})
    return out / "sample.mp3"


def cmd_sample(args) -> int:
    """The design lines read by any voice (e.g. one from the Voice Library), next to the candidates."""
    config = load_config()
    kind, _ = voice_of(config, args.lang, args.voice)
    path = sample_voice(args.lang, kind, args.voice_id, args.name, f"existing voice {args.voice_id}", config)
    print(f"  {args.name}: {rel(path)}")
    print(f"listen: {rel(design_index(args.lang, kind))}")
    return 0


def cmd_library(args) -> int:
    """Searches the Voice Library for the language and voice; --sample N records the design lines with the top N."""
    config = load_config()
    kind, _ = voice_of(config, args.lang, args.voice)
    query = {"page_size": args.limit, "language": args.lang.split("-")[0], "sort": "cloned_by_count"}
    if kind in ("male", "female"):
        query["gender"] = kind
    if args.search:
        query["search"] = args.search
    if args.use_case:
        query["use_cases"] = args.use_case
    voices = call("GET", "/v1/shared-voices", query=query).get("voices", [])
    for n, v in enumerate(voices, 1):
        notice = v.get("notice_period")
        about = (f"{v.get('name')}: {v.get('age', '?')}, {v.get('accent', '?')}, {v.get('descriptive', '')}, "
                 f"{v.get('use_case', '')}; {v.get('cloned_by_count', 0)} users; notice period "
                 f"{notice if notice is not None else 'none'} days; {(v.get('description') or '')[:120]}")
        print(f"{n:>2}. {v['voice_id']}  {about}")
        if n <= args.sample:
            slug = "".join(ch for ch in v.get("name", "voice").lower() if ch.isalnum())[:24] or "voice"
            path = sample_voice(args.lang, kind, v["voice_id"], slug, about, config)
            print(f"    sampled: {rel(path)}")
    if args.sample:
        print(f"listen: {rel(design_index(args.lang, kind))}")
    return 0


def find_design(lang: str, kind: str, generated_voice_id: str) -> str | None:
    for meta_path in design_dir(lang, kind).glob("*/design.json"):
        meta = json.loads(meta_path.read_text("utf8"))
        if any(i.get("generated_voice_id") == generated_voice_id for i in meta["items"]):
            return meta["description"]
    return None


def cmd_adopt(args) -> int:
    """Saves a Voice Design candidate as a voice in the account and makes it the language's voice of that kind."""
    config = load_config()
    kind, voice = voice_of(config, args.lang, args.voice)
    description = find_design(args.lang, kind, args.generated_voice_id) or voice.get("description", "")
    name = args.name or voice.get("name") or f"{CHARACTER} ({args.lang}, {kind})"
    result = call("POST", "/v1/text-to-voice", {
        "voice_name": name, "voice_description": description, "generated_voice_id": args.generated_voice_id})
    voice.update(voice_id=result["voice_id"], name=name, description=description, source="design")
    dump_json(CONFIG, config)
    print(f"{args.lang}/{kind}: {name} = {result['voice_id']} (saved in voice-config.json)")
    return 0


def cmd_use(args) -> int:
    """Makes an existing voice (in the account or the Voice Library) the language's voice of that kind."""
    config = load_config()
    kind, voice = voice_of(config, args.lang, args.voice)
    voice.update(voice_id=args.voice_id, source=args.source)
    if args.name:
        voice["name"] = args.name
    dump_json(CONFIG, config)
    print(f"{args.lang}/{kind}: {voice.get('name', '')} = {args.voice_id} (saved in voice-config.json)")
    return 0


def write_page(path: Path, title: str, intro: str, rows: list[tuple[str, str, str]]) -> None:
    items = "\n".join(
        f"<li><p><b>{html.escape(a)}</b><br><small>{html.escape(c)}</small></p>"
        f"<audio controls preload=none src=\"{html.escape(b)}\"></audio></li>"
        for a, b, c in rows
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "<!doctype html><meta charset=utf-8><title>" + html.escape(title) + "</title>"
        "<style>body{font:15px system-ui;max-width:760px;margin:2em auto;padding:0 16px}"
        "li{margin:0 0 1.2em}audio{width:100%}small{color:#776}</style>"
        f"<h1>{html.escape(title)}</h1><p>{html.escape(intro)}</p><ol>{items}</ol>",
        "utf8",
    )


REVIEW_COLOR_TOKENS = ("paper", "card", "ink", "ink-soft", "line", "accent", "accent-soft", "on-accent", "done",
                       "done-soft", "flag", "flag-soft", "link")
SITE_DEFAULT_COLORS = (":root{--paper:#FFF8E7;--ink:#3D2B1F;--ink-soft:#7A6555;--link:#6B5000}"
                       "@media(prefers-color-scheme:dark){:root{--paper:#1F1B17;--ink:#EFE6D8;--ink-soft:#B3A592;--link:#E2B93F}}")


def review_colors() -> str:
    """The app's review-page colors (`voice.reviewColors`: per scheme, `light` and `dark`, a token → `#RRGGBB` map) as
    CSS custom properties that override the template's defaults; empty when the app sets none."""
    colors = APP.get("voice.reviewColors") or {}
    css = ""
    for scheme in ("light", "dark"):
        values = colors.get(scheme) or {}
        for token, value in values.items():
            if token not in REVIEW_COLOR_TOKENS or not re.fullmatch(r"#[0-9A-Fa-f]{6}", str(value)):
                fail(f"voice.reviewColors.{scheme}.{token}: use one of {', '.join(REVIEW_COLOR_TOKENS)} with a #RRGGBB value")
        if values:
            rule = ":root{" + "".join(f"--{token}:{value};" for token, value in values.items()) + "}"
            css += rule if scheme == "light" else f"@media (prefers-color-scheme: dark){{{rule}}}"
    return css


def url_path(path: Path, start: Path) -> str:
    """`path` relative to the directory `start`, as a URL path: forward slashes on every OS, so a page generated on
    Windows is byte-identical to one generated on a Mac."""
    return os.path.relpath(path, start).replace(os.path.sep, "/")


def review_page(lang: str, kinds: list[str], page: Path, site: bool, config: dict, manifest: dict) -> tuple[str, int]:
    """The review page's HTML for `lang` (clip paths relative to `page`) and how many lines it lists. A clip plays only
    while it says the current text; a line whose voices are all missing or stale is listed as waiting, text only.
    Each clip carries its render hash as a version, so the page busts caches and shows a new take as unheard."""
    keys = clip_keys(config, lang)
    texts, english = catalog_texts(lang, keys), catalog_texts(APP.source_language, keys)
    parts = clip_phrases()
    direction = direction_map()
    voices = [{"kind": k, "name": language(config, lang)["voices"][k].get("name") or k} for k in kinds]
    states = {k: pending(lang, k, config, manifest) for k in kinds}
    rows = []
    for clip in keys:
        if clip not in texts:
            continue
        clips, versions = {}, {}
        for k in kinds:
            if states[k][clip] is None:
                version = manifest_clips(manifest, lang, k)[clip].get("renderHash", "")[:12]
                path = url_path(clip_path(lang, k, clip), page.parent)
                clips[k], versions[k] = f"{path}?v={version}" if version else path, version
        line, part, count = parts.get(clip, ({}, 1, 1))
        rows.append({"id": clip, "text": texts[clip], "en": english.get(clip, ""), "flavor": line.get("flavor") or "",
                     "slot": ", ".join(line.get("tags", ["ui"])), "tags": direction.get(clip, {}).get("tags", ""),
                     "joke": line["id"] if count > 1 else "", "part": part, "parts": count,
                     "clips": clips, "versions": versions})
    recorded = set(manifest.get("languages", {})) & set(config.get("languages", {}))
    languages = [{"code": code, "name": LANGUAGE_NAMES.get(code, code), "recorded": True}
                 for code in sorted(recorded, key=lambda code: LANGUAGE_NAMES.get(code, code))]
    flavors = list(dict.fromkeys(row["flavor"] for row in rows if row["flavor"]))
    data = {"lang": lang, "languageName": LANGUAGE_NAMES.get(lang, lang), "voices": voices, "rows": rows, "site": site,
            "languages": languages, "title": REVIEW_TITLE, "notice": SITE_NOTICE, "app": APP.require("name"),
            "flavors": flavors}
    html_text = REVIEW_TEMPLATE.read_text("utf8").replace("/*COLORS*/", review_colors()).replace(
        "/*DATA*/null", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    return html_text, sum(1 for r in rows if r["clips"])


def recorded_languages(config: dict, manifest: dict) -> list[str]:
    return sorted(lang for lang in manifest.get("languages", {}) if lang in config.get("languages", {}))


def site_files(config: dict, manifest: dict) -> dict[Path, str]:
    """Everything the voices repository holds besides the clips, as it should be now: a review page per recorded
    language (both voices), the index linking them, robots.txt, README and LICENSE. Pages serves it for friends."""
    site = VOICE.parent
    files = {}
    langs = recorded_languages(config, manifest)
    for lang in langs:
        page = site / "review" / f"{lang}.html"
        kinds = [k for k in KINDS if manifest_clips(manifest, lang, k)]
        files[page] = review_page(lang, kinds, page, True, config, manifest)[0]
    links = "".join(f'<li><a href="review/{html.escape(l)}.html">{html.escape(LANGUAGE_NAMES.get(l, l))}</a></li>'
                    for l in langs)
    title = html.escape(f"{REVIEW_TITLE} review", quote=False)
    files[site / "index.html"] = (
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, '
        f'initial-scale=1"><meta name="robots" content="noindex, nofollow, noarchive"><title>{title}</title>'
        f"<style>{SITE_DEFAULT_COLORS}{review_colors()}body{{font:16px/1.5 system-ui;max-width:640px;margin:3em auto;"
        "padding:0 16px;background:var(--paper);color:var(--ink)}a{color:var(--link)}small{color:var(--ink-soft)}</style>"
        f"<h1>{title}</h1><p>Thanks for helping! Pick a language, listen, and flag anything that sounds "
        f"off. When you're done, copy your notes and send them back.</p><ul>{links}</ul>"
        f"<p><small>{html.escape(SITE_NOTICE)}</small></p></html>\n")
    files[site / "robots.txt"] = "User-agent: *\nDisallow: /\n"
    files[site / ".nojekyll"] = ""
    name, display = APP.require("name"), APP.require("displayName")
    # "<character>, in <app>"; just the character when the app is named after it.
    whose = CHARACTER if CHARACTER == display else f"{CHARACTER}, in {display}"
    holder = APP.get("voice.copyrightHolder") or display
    repo = APP.get("voice.repo")
    pages = f": https://{repo.split('/')[0]}.github.io/{repo.split('/')[1]}/" if repo and "/" in repo else ""
    files[site / "README.md"] = (
        f"# {CHARACTER}'s voices\n\n"
        f"The recorded lines of {whose}, made with ElevenLabs:\n\n"
        "- `Voice/<lang>/<voice>/<id>.m4a` and `Voice/manifest.json`, bundled by the app (this repository is the "
        f"`voices` submodule of {name}, shared by its Mac and Windows apps).\n"
        f"- `review/<lang>.html`, a page for friends to listen through and flag what sounds off{pages}\n\n"
        f"Everything here is generated by `laching/tools/voice.py` in {name} (`render`, `retake`, `site`, "
        "`publish`); don't edit it by hand.\n\n"
        f"## License\n\n{SITE_NOTICE} See [LICENSE](LICENSE).\n")
    files[site / "LICENSE"] = (
        f"Copyright (c) {holder}. All rights reserved.\n\n"
        "The recordings, texts and pages in this repository are published only so friends can review them. No license "
        "is granted to copy, modify, redistribute, or otherwise reuse them, including to train or evaluate machine "
        "learning models.\n")
    return files


def stale_site(config: dict, manifest: dict) -> list[Path]:
    """Site files that differ from what `site_files` would write (missing ones included), and old language pages."""
    files = site_files(config, manifest)
    return companion.write_outputs(files, check=True) + old_site_pages(files)


def sync_site(config: dict, manifest: dict) -> list[Path]:
    """Bring the friends' site in line with the clips; returns what changed."""
    files = site_files(config, manifest)
    old = old_site_pages(files)
    for path in old:
        path.unlink()
    return companion.write_outputs(files, check=False) + old


def old_site_pages(files: dict[Path, str]) -> list[Path]:
    """The review pages of languages the site no longer has."""
    return [p for p in sorted(VOICE.parent.glob("review/*.html")) if p not in files]


def cmd_review(args) -> int:
    """The review pages for the owner: every line in bank order (a joke's parts together), the voices side by side, a
    listen-through mode, retake and text flags, and a summary to paste back to Claude (tools/voice-review.html). Writes every
    recorded language, so the page's language menu works; prints the one asked for (default: the first)."""
    config, manifest = load_config(), load_manifest()
    langs = recorded_languages(config, manifest)
    if args.lang and args.lang not in langs:
        langs.append(args.lang)
    if not langs:
        fail("nothing recorded yet: render a language first")
    for lang in langs:
        page = WORK / "review" / f"{lang}.html"
        kinds = [args.voice] if args.voice else ready_voices(config, lang)
        html_text, count = review_page(lang, kinds, page, False, config, manifest)
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(html_text, "utf8")
        if lang == (args.lang or langs[0]):
            shown = (page, count, len(kinds))
    print(f"{shown[1]} lines, {shown[2]} voice(s): {rel(shown[0])}")
    return 0


def cmd_site(args) -> int:
    """Regenerate the friends' site in the voices repository (render and retake do this on their own)."""
    changed = sync_site(load_config(), load_manifest())
    print(f"site: {len(changed)} file(s) updated" + "".join(f"\n  {rel(p)}" for p in changed))
    return 0


def git(*cmd: str, cwd: Path | None = None) -> str:
    """git's stdout in `cwd` (the app's root), failing with its stderr."""
    if (result := companion.git(cwd or ROOT, *cmd)).returncode != 0:
        fail(f"git {cmd[0]} failed in {rel(cwd or ROOT)}: {result.stderr.strip()[-800:]}")
    return result.stdout.strip()


def default_branch(site: Path) -> str:
    """The voices repository's default branch on `origin` (`main`, `master`…), which publish pushes to."""
    found = companion.git(site, "ls-remote", "--symref", "origin", "HEAD")
    for line in found.stdout.splitlines():
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
            return line[len("ref: refs/heads/"):-len("\tHEAD")]
    fail(f"can't read the default branch of {rel(site)}'s origin: {found.stderr.strip()[-300:]}")


def cmd_publish(args) -> int:
    """Ship the voices: check, refresh the site, commit and push the voices repository, then stage its new pointer
    here (commit it with the change that needed it). Pushing first means the pointer never names an unpushed commit."""
    config, manifest = load_config(), load_manifest()
    sync_site(config, manifest)
    if cmd_check(args):
        fail("fix the problems above first")
    site = VOICE.parent
    if not (site / ".git").exists():
        fail(f"{rel(site)} isn't checked out: run `git submodule update --init`")
    git("add", "-A", cwd=site)
    if git("status", "--porcelain", cwd=site):
        git("commit", "-q", "-m", args.message, cwd=site)
        print(f"voices: committed {git('log', '-1', '--format=%h %s', cwd=site)}")
    else:
        print("voices: nothing new to commit")
    # By refspec: `git submodule update` leaves the checkout detached, and a plain `push origin HEAD` refuses that.
    # If another machine published first, this fails as a non-fast-forward instead of overwriting its clips.
    branch = default_branch(site)
    pushed = companion.git(site, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
    if pushed.returncode != 0:
        fail(f"push failed: {pushed.stderr.strip()[-600:]}\nIf the voices repo moved on elsewhere: "
             f"`git -C {rel(site)} pull --rebase origin {branch}`, then voice.py site and publish again.")
    git("add", rel(site))
    print(f"voices: pushed; {rel(site)} staged here at {git('rev-parse', '--short', 'HEAD', cwd=site)}, commit it")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, func, *, voice=True, lang=True):
        p = sub.add_parser(name)
        if lang:
            p.add_argument("--lang", required=lang != "optional")
        if voice:
            p.add_argument("--voice", help="one of voice.voices; default: the language's default voice")
        p.set_defaults(func=func)
        return p

    add("status", cmd_status, voice=False, lang=False).add_argument("--json", action="store_true")
    add("check", cmd_check, voice=False, lang=False)
    p = add("design", cmd_design)
    p.add_argument("--description", help="try a different prompt; kept in voice-config.json only on adopt")
    p.add_argument("--seed", type=int)
    p = add("library", cmd_library)
    p.add_argument("--search")
    p.add_argument("--use-case", help="e.g. conversational, characters_animation")
    p.add_argument("--limit", type=int, default=12)
    p.add_argument("--sample", type=int, default=0, help="record the design lines with the top N")
    p = add("sample", cmd_sample)
    p.add_argument("voice_id")
    p.add_argument("--name", required=True)
    p = add("adopt", cmd_adopt)
    p.add_argument("generated_voice_id")
    p.add_argument("--name")
    p = add("use", cmd_use)
    p.add_argument("voice_id")
    p.add_argument("--name")
    p.add_argument("--source", default="library", choices=("library", "design", "account"))
    p = add("render", cmd_render)
    p.add_argument("--only")
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--prune", action="store_true")
    p = add("retake", cmd_retake)
    p.add_argument("id", help="one id or several, comma-separated")
    p.add_argument("--yes", action="store_true")
    add("review", cmd_review, lang="optional")  # --voice: one voice only; default both
    add("site", cmd_site, voice=False, lang=False)
    add("publish", cmd_publish, voice=False, lang=False).add_argument("-m", "--message", required=True)
    configure()
    args = parser.parse_args()
    if getattr(args, "voice", None) and args.voice not in KINDS:
        fail(f"--voice must be one of {', '.join(KINDS)}")
    return args.func(args)


if __name__ == "__main__":
    companion.utf8_console()
    sys.exit(main())
