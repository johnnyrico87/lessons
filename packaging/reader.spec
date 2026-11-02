# PyInstaller spec for the read-only lessons reader app.
#
# Build:  cd packaging && uv run --with pyinstaller --with fastapi --with uvicorn \
#             pyinstaller reader.spec --noconfirm
#
# Produces dist/lessons (onedir). The app boots uvicorn on a free loopback port
# and opens the browser. Reader-only: no ingest/extract/Ollama/Whisper deps.
#
# NOT signed/notarized — Gatekeeper will flag it. Signing + notarization + a
# universal2 build are explicit follow-ups (need an Apple Developer cert).

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

SPEC_DIR = Path(SPECPATH)
REPO = SPEC_DIR.parent
SRC = REPO / "src" / "lessons"

# Bundle the static UI, and a baked graph bundle if one has been placed here.
datas = [(str(SRC / "web" / "static"), "lessons/web/static")]
baked = SPEC_DIR / "graph-bundle.json"
if baked.exists():
    datas.append((str(baked), "."))   # lands at _MEIPASS/graph-bundle.json

# uvicorn/fastapi/starlette pull many modules dynamically; collect them so the
# frozen app finds them at runtime.
hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("fastapi")
    + collect_submodules("starlette")
    + collect_submodules("anyio")
)

a = Analysis(
    [str(SPEC_DIR / "app_main.py")],
    pathex=[str(REPO / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Exclude the heavy maintainer-only pass deps — the reader never uses them.
    excludes=["yt_dlp", "faster_whisper", "ollama", "av", "youtube_transcript_api"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="lessons",
    console=True,          # keep a console for v1 so startup/errors are visible
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,      # this machine's arch; universal2 is a follow-up
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    name="lessons",
)
