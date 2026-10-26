#!/usr/bin/env python3
"""Transcribe the lecture mp3s with mlx-whisper (Apple Silicon).

Writes transcripts/lNN.txt (plain text) and transcripts/lNN.srt (timestamps)
for every mp3/lNN.mp3. Existing transcripts are skipped, so it can be re-run.
The language is auto-detected per file unless overridden.

Setup:   see README.md (needs Python 3.12/3.13 and Apple Silicon)
Run:     ~/whisper-env/bin/python transcribe.py
Options: --only l01 l02          only these lectures
         --language en           force a language for all files
         --lang l08=no           force a language for one file (repeatable)
         --model REPO            Hugging Face model (default: whisper-large-v3-turbo)
"""
import argparse
import glob
import os

try:
    import mlx_whisper
except ImportError:
    raise SystemExit("mlx-whisper is not installed (Apple Silicon only). See README.md for setup.")


def stamp(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mp3-dir", default="mp3")
    ap.add_argument("--out", default="transcripts")
    ap.add_argument("--model", default="mlx-community/whisper-large-v3-turbo")
    ap.add_argument("--language")
    ap.add_argument("--lang", action="append", default=[], metavar="LABEL=LANG")
    ap.add_argument("--only", nargs="+")
    args = ap.parse_args()

    per_file = dict(x.split("=", 1) for x in args.lang)
    os.makedirs(args.out, exist_ok=True)
    for path in sorted(glob.glob(os.path.join(args.mp3_dir, "*.mp3"))):
        label = os.path.splitext(os.path.basename(path))[0]
        if args.only and label not in args.only:
            continue
        txt = os.path.join(args.out, label + ".txt")
        if os.path.exists(txt):
            print(f"{label}: exists")
            continue
        print(f"{label}: transcribing...", flush=True)
        res = mlx_whisper.transcribe(
            path,
            path_or_hf_repo=args.model,
            language=per_file.get(label, args.language),
            condition_on_previous_text=False,
            verbose=None,
        )
        with open(os.path.join(args.out, label + ".srt"), "w", encoding="utf-8") as f:
            for i, seg in enumerate(res["segments"], 1):
                f.write(f"{i}\n{stamp(seg['start'])} --> {stamp(seg['end'])}\n{seg['text'].strip()}\n\n")
        with open(txt, "w", encoding="utf-8") as f:
            f.write("\n".join(seg["text"].strip() for seg in res["segments"]) + "\n")
        print(f"{label}: done (language: {res.get('language')})", flush=True)


if __name__ == "__main__":
    main()
