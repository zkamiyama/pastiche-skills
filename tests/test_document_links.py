"""Deleted compatibility references must not survive as broken local links."""
from pathlib import Path
import re
import unittest
ROOT=Path(__file__).resolve().parents[1]
class DocumentLinksTests(unittest.TestCase):
    def test_repository_relative_markdown_links_resolve(self):
        missing=[]
        for path in [ROOT/'README.md', *ROOT.joinpath('docs').rglob('*.md'), *ROOT.joinpath('skills').rglob('*.md')]:
            for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)',path.read_text(encoding='utf-8')):
                if '://' in target or target.startswith(('#','mailto:')):continue
                target=target.split('#')[0]
                if target and not (path.parent/target).exists():missing.append((str(path.relative_to(ROOT)),target))
        self.assertEqual(missing,[])
