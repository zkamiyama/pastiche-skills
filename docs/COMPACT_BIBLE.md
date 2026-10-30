# Compact Style Bible (AI-facing Markdown)

The guide prints one concise global caveat, then quantitative targets,
supported techniques and one illustration per pattern, and lexical usage.
Repeated phrases such as "newly created", "model estimated", "evidence",
and per-item source verification are omitted from the Markdown.

Original analysis, support IDs, work counts, all illustrations, source
bindings, and detailed lexical explanations remain in the private JSON
artifacts. The rendered example is new prose, not a quotation or new
evidence. No underlying judgments or caches are recomputed.

To rebuild a completed private profile without any LLM requests:

\`\`\`bash
python3 skills/pastiche-profile/scripts/render_bible.py --profile /private/profile
python3 skills/pastiche-profile/scripts/render_bible.py --profile /private/profile --apply
\`\`\`

The first command previews character reduction. The second backs up
the four Markdown guides and replaces only those four files.
The underlying JSON, SQLite checkpoints, observations, and model audit
are never modified. Verify that the installed scripts match the pushed
revision before refreshing an existing profile. Subsequent ordinary
\`run.py\` calls use the same compact rendering automatically.
