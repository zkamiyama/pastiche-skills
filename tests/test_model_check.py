"""Finite model checks remain a mandatory part of offline regression testing."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'verification'))
from model_check import run_checks

class BoundedModelTests(unittest.TestCase):
    def test_exhaustive_finite_transition_model_and_noninterference(self):
        result=run_checks()
        self.assertEqual(result['status'],'passed')
        self.assertGreater(result['checked'],40_000)
        self.assertGreater(result['rejected'],1_000)
        self.assertEqual(result['private_view_pairs'],27)
