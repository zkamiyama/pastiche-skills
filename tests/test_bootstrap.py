"""Tests for automatic first-run dependency setup (no network/pip execution)."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import os
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "pastiche-profile" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import bootstrap


class BootstrapTests(unittest.TestCase):
    def test_automatic_by_default_and_explicit_opt_out(self):
        self.assertTrue(bootstrap.needs_dictionary(["/tmp/corpus"]))
        self.assertTrue(bootstrap.needs_dictionary(["/tmp/corpus", "--morphology", "fugashi"]))
        self.assertTrue(bootstrap.needs_dictionary(["/tmp/corpus", "--morphology=auto"]))
        for argv in (["-h"], ["--help"], ["--check", "/not/real"],
                     ["/tmp/corpus", "--morphology", "none"],
                     ["/tmp/corpus", "--morphology=none"],
                     ["/tmp/corpus", "--no-auto-install"]):
            with self.subTest(argv=argv):
                self.assertFalse(bootstrap.needs_dictionary(argv))

    def test_reuse_cache_and_install_to_venv_only(self):
        with TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {"XDG_CACHE_HOME": temporary}):
                dest = bootstrap.cache_environment()
                interpreter = bootstrap.environment_python(dest)
                calls = []
                def fake_venv(*args, **kwargs):
                    self.assertTrue(kwargs["with_pip"])
                    class EnvironmentBuilder:
                        def create(self, path):
                            self_outer.assertEqual(path, dest)
                            interpreter.parent.mkdir(parents=True, exist_ok=True)
                            interpreter.write_bytes(b"test interpreter")
                    self_outer = self
                    return EnvironmentBuilder()
                def fake_run(command, **kwargs):
                    calls.append((command,kwargs))
                    class Result:
                        returncode=0
                    return Result()
                with patch.object(bootstrap.venv, "EnvBuilder", side_effect=fake_venv), \
                     patch.object(bootstrap.subprocess, "run", side_effect=fake_run):
                    self.assertEqual(bootstrap.prepare_environment(), interpreter)
                    self.assertTrue((dest / bootstrap.READY_FILE).exists())
                    self.assertEqual(bootstrap.prepare_environment(), interpreter)
                pip_invocations = [cmd for cmd, _ in calls if len(cmd) > 2 and cmd[1:3] == ["-m", "pip"]]
                self.assertEqual(len(pip_invocations), 1)
                self.assertEqual(pip_invocations[0][0], str(interpreter))
                self.assertIn("-r",pip_invocations[0])

    def test_bootstrap_entrypoint_reexecutes_without_changing_original_args(self):
        newpython=Path("/tmp/test-pastiche-venv/bin/python")
        script=Path("/path/to/run.py")
        args=["/path/to/corpus", "--api-base", "http://example.test/llm/v1"]
        with patch.object(bootstrap, "cache_environment", return_value=Path("/tmp/test-pastiche-venv")), \
             patch.object(bootstrap, "prepare_environment", return_value=newpython), \
             patch.object(bootstrap.os, "execv") as execv:
            bootstrap.bootstrap_entrypoint(script,args)
            execv.assert_called_once_with(str(newpython),[str(newpython),str(script),*args])

    def test_requires_plus_distribution_not_upstream(self):
        import importlib.metadata as metadata
        with patch.object(bootstrap.metadata, "version", side_effect=metadata.PackageNotFoundError("fugashi-plus")):
            self.assertFalse(bootstrap.dictionary_available())
        self.assertIn("fugashi-plus[unidic-lite]==1.5.2.post1", bootstrap.REQUIREMENTS.read_text(encoding="utf-8"))

    def test_no_bootstrap_for_help_or_opt_out(self):
        with patch.object(bootstrap,"dictionary_available") as available, \
             patch.object(bootstrap,"prepare_environment") as prepare:
            bootstrap.bootstrap_entrypoint(Path("run.py"),["--help"])
            bootstrap.bootstrap_entrypoint(Path("run.py"),["folder","--no-auto-install"])
            bootstrap.bootstrap_entrypoint(Path("run.py"),["folder","--morphology","none"])
            available.assert_not_called()
            prepare.assert_not_called()

if __name__ == "__main__":
    unittest.main()
