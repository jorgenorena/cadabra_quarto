import os
import time
import subprocess
from collections import defaultdict
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from fix_cadabra_latex import fix_cadabra_file

# -----------------------------
# Configuration
# -----------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Command used to run Cadabra
# Adjust if your executable is named differently
CADABRA_CMD = ["cadabra2"]

LOGS_DIR = PROJECT_ROOT / "logs"

DEBOUNCE_SECONDS = 0.8


# -----------------------------
# Build logic
# -----------------------------

def _system_env() -> dict[str, str]:
    """Return a copy of os.environ without virtualenv overrides."""
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    # Restore PATH: remove the venv bin directory
    venv_bin = os.environ.get("VIRTUAL_ENV")
    if venv_bin:
        venv_bin_dir = str(Path(venv_bin) / "bin")
        env["PATH"] = os.pathsep.join(
            p for p in env.get("PATH", "").split(os.pathsep)
            if p != venv_bin_dir
        )
    return env


def _extract_frontmatter(text: str) -> list[str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return []

    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return lines[1:idx]

    return []


def _parse_cadabra_deps_from_frontmatter(lines: list[str]) -> list[str]:
    deps: list[str] = []
    i = 0

    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()

        if not stripped.startswith("cadabra-deps:"):
            i += 1
            continue

        base_indent = len(raw) - len(raw.lstrip(" "))
        inline_value = stripped.split(":", 1)[1].strip()

        if inline_value.startswith("[") and inline_value.endswith("]"):
            body = inline_value[1:-1].strip()
            if body:
                for part in body.split(","):
                    item = part.strip().strip('"\'')
                    if item:
                        deps.append(item)

        i += 1
        while i < len(lines):
            child_raw = lines[i]
            child_stripped = child_raw.strip()
            child_indent = len(child_raw) - len(child_raw.lstrip(" "))

            if child_stripped and child_indent <= base_indent:
                break

            if child_stripped.startswith("- "):
                item = child_stripped[2:].strip().strip('"\'')
                if item:
                    deps.append(item)
            i += 1

    return deps


def discover_inverse_deps() -> dict[Path, set[Path]]:
    """Build inverse dependency map: cdb file -> set of qmd files that depend on it."""
    inverse: dict[Path, set[Path]] = defaultdict(set)

    for qmd_file in PROJECT_ROOT.rglob("*.qmd"):
        # Skip generated/hidden/vendor directories.
        if any(part.startswith(".") for part in qmd_file.parts):
            continue
        if "_site" in qmd_file.parts:
            continue

        frontmatter = _extract_frontmatter(qmd_file.read_text(encoding="utf-8"))
        if not frontmatter:
            continue

        rel_deps = _parse_cadabra_deps_from_frontmatter(frontmatter)
        for rel_dep in rel_deps:
            dep_path = (PROJECT_ROOT / rel_dep).resolve()
            inverse[dep_path].add(qmd_file.resolve())

    return dict(inverse)


def run_cadabra(cdb_file: Path) -> None:
    cmd = CADABRA_CMD + [str(cdb_file)]
    log_file = LOGS_DIR / f"{cdb_file.stem}.txt"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    print(f"Project root: {PROJECT_ROOT}")
    result = subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_system_env(),
    )

    # tee: print to console and write to log
    if result.stdout:
        print(result.stdout, end="")
    log_file.write_text(result.stdout, encoding="utf-8")

    if result.returncode != 0:
        raise RuntimeError(
            f"Cadabra failed for {cdb_file}\n"
            f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
        )


def touch(path: Path) -> None:
    path.touch()


def build_one(cdb_file: Path, inverse_deps: dict[Path, set[Path]]) -> None:
    cdb_file = cdb_file.resolve()
    dependents = inverse_deps.get(cdb_file)
    if not dependents:
        return

    print(f"[build] Running Cadabra on {cdb_file.relative_to(PROJECT_ROOT)}")
    run_cadabra(cdb_file)

    tex_file = PROJECT_ROOT / "results" / f"{cdb_file.stem}.tex"
    if tex_file.exists():
        fix_cadabra_file(tex_file)
        print(f"[build] Fixed LaTeX in {tex_file.relative_to(PROJECT_ROOT)}")

    for qmd_file in sorted(dependents):
        touch(qmd_file)
        print(f"[build] Touched {qmd_file.relative_to(PROJECT_ROOT)} for Quarto rebuild")


def initial_build(inverse_deps: dict[Path, set[Path]]) -> None:
    print("[init] Initial build")
    for cdb_file in sorted(inverse_deps):
        try:
            build_one(cdb_file, inverse_deps)
        except Exception as e:
            print(f"[error] {e}")


# -----------------------------
# Watcher
# -----------------------------

class CadabraHandler(FileSystemEventHandler):
    def __init__(self, inverse_deps: dict[Path, set[Path]]):
        self.last_run = {}
        self.inverse_deps = inverse_deps

    def _refresh_inverse_deps(self) -> None:
        old_keys = set(self.inverse_deps.keys())
        self.inverse_deps = discover_inverse_deps()
        print(f"[deps] Reloaded dependency map ({len(self.inverse_deps)} cdb file(s))")

        new_keys = set(self.inverse_deps.keys()) - old_keys
        for cdb_file in sorted(new_keys):
            print(f"[deps] New dependency detected: {cdb_file.relative_to(PROJECT_ROOT)}")
            try:
                build_one(cdb_file, self.inverse_deps)
            except Exception as e:
                print(f"[error] {e}")

    def should_handle(self, path: Path) -> bool:
        return path.suffix == ".cdb" and path.resolve() in self.inverse_deps

    def should_refresh(self, path: Path) -> bool:
        return path.suffix == ".qmd"

    def is_debounced(self, path: Path) -> bool:
        now = time.time()
        last = self.last_run.get(path, 0.0)
        if now - last < DEBOUNCE_SECONDS:
            return True
        self.last_run[path] = now
        return False

    def on_modified(self, event):
        if event.is_directory:
            return

        path = Path(event.src_path).resolve()
        if self.should_refresh(path):
            if not self.is_debounced(path):
                self._refresh_inverse_deps()
            return

        if not self.should_handle(path):
            return
        if self.is_debounced(path):
            return

        try:
            build_one(path, self.inverse_deps)
        except Exception as e:
            print(f"[error] {e}")

    def on_created(self, event):
        if event.is_directory:
            return

        path = Path(event.src_path).resolve()
        if self.should_refresh(path):
            if not self.is_debounced(path):
                self._refresh_inverse_deps()
            return

        self.on_modified(event)

    def on_deleted(self, event):
        if event.is_directory:
            return

        path = Path(event.src_path).resolve()
        if self.should_refresh(path):
            # No debounce on delete to avoid stale references.
            self._refresh_inverse_deps()

    def on_moved(self, event):
        if event.is_directory:
            return

        src = Path(event.src_path).resolve()
        dst = Path(event.dest_path).resolve()
        if self.should_refresh(src) or self.should_refresh(dst):
            self._refresh_inverse_deps()


def main():
    inverse_deps = discover_inverse_deps()
    if not inverse_deps:
        print("[init] No cadabra-deps found in any .qmd frontmatter")
    else:
        print(f"[init] Found {len(inverse_deps)} Cadabra dependency file(s)")

    initial_build(inverse_deps)

    observer = Observer()
    handler = CadabraHandler(inverse_deps)
    observer.schedule(handler, str(PROJECT_ROOT), recursive=True)
    observer.start()

    print("[watch] Watching for .cdb changes...")
    print("[watch] Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()

    observer.join()


if __name__ == "__main__":
    main()