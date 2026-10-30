#!/usr/bin/env python3
"""Regenerate four compact guide Markdown files from completed cached artifacts.

No LLM calls and no changes to analysis checkpoints or JSON.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import lexical
import lexical_review
import llm_style
import pastiche

def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))

def render(profile: Path, *, apply: bool = False) -> dict:
    required = ["style_profile.json", "lexical_profile.json", "lexical_judgments.json",
                "qualitative_patterns.json", "qualitative_summary.json",
                "style_llm_audit.json", "narrative_rules.json", "llm_progress.json"]
    for name in required:
        if not (profile / name).is_file():
            raise ValueError("missing completed artifact: " + name)
    if load(profile / "llm_progress.json").get("state") != "completed":
        raise ValueError("style analysis not completed")
    audit = load(profile / "style_llm_audit.json")
    patterns = load(profile / "qualitative_patterns.json")["patterns"]
    summary = load(profile / "qualitative_summary.json")["sections"]
    illustrations = (load(profile / "guide_examples.json")
                     if (profile / "guide_examples.json").is_file() else None)
    rendered = {
        "lexical_bible.md": lexical.private_guide(load(profile / "lexical_profile.json")),
        "lexical_interpretation.md": lexical_review.make_guide(
            load(profile / "lexical_judgments.json")),
        "qualitative_bible.md": llm_style.qualitative_guide(
            patterns, total_works=audit["total_works"],
            sampled_works=audit["sampled_works"], chunks=audit["selected_chunks"],
            synthesis=summary, illustrations=illustrations),
    }
    with tempfile.TemporaryDirectory(prefix="compact-bible-") as work:
        temp = Path(work)
        for name, content in rendered.items():
            (temp / name).write_text(content, encoding="utf-8")
        story = profile / "narrative_bible.md"
        complete_story = ((profile / "story_progress.json").is_file()
                          and load(profile / "story_progress.json").get("state") == "completed")
        code = pastiche.command_guide(SimpleNamespace(
            profile=profile / "style_profile.json",
            lexical=temp / "lexical_bible.md",
            notes=profile / "narrative_rules.json",
            qualitative=temp / "qualitative_bible.md",
            narrative_structure=story if story.is_file() and complete_story else None,
            lexical_review=temp / "lexical_interpretation.md",
            out=temp / "style_bible.md"))
        if code:
            raise ValueError("guide assembly failed")
        rendered["style_bible.md"] = (temp / "style_bible.md").read_text(encoding="utf-8")
    old = (profile / "style_bible.md").read_text(encoding="utf-8") if (profile / "style_bible.md").exists() else ""
    outcome = {"previous_chars": len(old), "new_chars": len(rendered["style_bible.md"]),
               "reduction_pct": round(100 * (len(old) - len(rendered["style_bible.md"]))
                                      / max(1, len(old)), 1),
               "files": list(rendered), "model_requests": 0}
    if not apply:
        return outcome
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = profile / (".bible-backup-" + stamp)
    backup.mkdir(mode=0o700)
    for name in rendered:
        if (profile / name).exists():
            shutil.copy2(profile / name, backup / name)
    for name, content in rendered.items():
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=profile,
                                         prefix=".compact-", suffix=".md", delete=False) as f:
            f.write(content)
            destination = Path(f.name)
        os.replace(destination, profile / name)
    outcome["backup"] = str(backup)
    outcome["updated"] = True
    return outcome

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(render(args.profile.resolve(), apply=args.apply),
                     ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
