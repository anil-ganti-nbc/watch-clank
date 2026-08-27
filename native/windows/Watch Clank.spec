# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

root = Path(SPECPATH).parents[1]
revision_file = root / "native" / "windows" / "generated" / "build_revision.txt"

a = Analysis(
    [str(root / "native" / "windows" / "launcher.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / "app" / "templates"), "app/templates"),
        (str(root / "alembic"), "alembic"),
        (str(root / "alembic.ini"), "."),
        *([(str(revision_file), ".")] if revision_file.exists() else []),
    ],
    hiddenimports=["scripts.run_pipeline", "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, exclude_binaries=False, name="Watch Clank", console=False)
