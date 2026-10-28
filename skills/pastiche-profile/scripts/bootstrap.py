#!/usr/bin/env python3
"""First-run, opt-out dependency setup for the Pastiche Profile entrypoint.

`npx skills add` installs files, not Python packages. When run.py is invoked
for actual profiling, this module ensures Japanese lexical tokenization is
available in a dedicated, reusable venv. It never reads the user's corpus.
"""
from __future__ import annotations

import hashlib
from importlib import metadata
import os
from pathlib import Path
import subprocess
import sys
import venv

HERE = Path(__file__).resolve().parent
REQUIREMENTS = HERE / "requirements-lexical.txt"
READY_FILE = ".pastiche-ready"


def needs_dictionary(argv: list[str]) -> bool:
    """Do not install for help, checks, or an explicitly disabled tokenizer."""
    if ("--help" in argv or "-h" in argv or "--check" in argv
            or "--no-auto-install" in argv):
        return False
    for i, arg in enumerate(argv):
        if arg == "--morphology" and i + 1 < len(argv):
            return argv[i + 1] != "none"
        if arg.startswith("--morphology="):
            return arg.split("=", 1)[1] != "none"
    return True  # run.py defaults to --morphology auto.


def dictionary_available() -> bool:
    """Only accept the fork distribution, not another package using the same import."""
    try:
        metadata.version("fugashi-plus")
        from fugashi import Tagger
        import unidic_lite
        Tagger()
        return True
    except (metadata.PackageNotFoundError, ImportError, RuntimeError, OSError, ValueError):
        return False


def cache_environment() -> Path:
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    version = f"py{sys.version_info.major}.{sys.version_info.minor}"
    digest = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()[:12]
    return cache / "pastiche-skills" / f"{version}-{digest}"


def environment_python(environment: Path) -> Path:
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _check_environment(python: Path) -> bool:
    if not python.is_file():
        return False
    command = [str(python), "-c",
               "from importlib import metadata; metadata.version('fugashi-plus'); "
               "from fugashi import Tagger; import unidic_lite; Tagger()"]
    result = subprocess.run(command, capture_output=True, text=True)
    return result.returncode == 0


def prepare_environment() -> Path:
    """Set up an isolated environment once per Python version and requirements hash."""
    environment = cache_environment()
    python = environment_python(environment)
    ready = environment / READY_FILE
    if ready.is_file() and _check_environment(python):
        return python

    environment.parent.mkdir(parents=True, exist_ok=True)
    lock_path = environment.parent / ".install.lock"
    with lock_path.open("a+") as lock:
        try:
            import fcntl
        except ImportError:
            fcntl = None
        if fcntl is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            if ready.is_file() and _check_environment(python):
                return python
            print("[pastiche] 初回準備: 専用Python環境に日本語辞書を導入します。"
                  "コーパス本文は送信しません。", file=sys.stderr, flush=True)
            venv.EnvBuilder(with_pip=True).create(environment)
            command = [str(python), "-m", "pip", "install", "--no-input",
                       "--disable-pip-version-check", "-r", str(REQUIREMENTS)]
            subprocess.run(command, check=True)
            if not _check_environment(python):
                raise RuntimeError("日本語辞書の動作確認に失敗しました")
            ready.write_text(
                f"requirements={hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()}\n",
                encoding="utf-8"
            )
            print("[pastiche] 初回準備が完了しました。次回からは再利用します。",
                  file=sys.stderr, flush=True)
            return python
        finally:
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def bootstrap_entrypoint(script: Path, argv: list[str]) -> None:
    """Re-execute run.py in the isolated interpreter when necessary."""
    if not needs_dictionary(argv):
        return
    environment = cache_environment()
    python = environment_python(environment)
    # Isolate the fork even when upstream fugashi is installed in caller Python.
    if Path(sys.executable).resolve() == python.resolve():
        if not dictionary_available():
            raise RuntimeError("仮想環境内で fugashi-plus と辞書を読み込めません")
        return
    python = prepare_environment()
    print("[pastiche] 専用Python環境で解析を開始します。", file=sys.stderr, flush=True)
    os.execv(str(python), [str(python), str(script), *argv])


if __name__ == "__main__":
    try:
        print(prepare_environment())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"依存パッケージの初期準備に失敗: {exc}", file=sys.stderr)
        sys.exit(2)
