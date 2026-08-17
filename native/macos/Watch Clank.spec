# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os

root = Path(SPECPATH).parents[1]
revision = os.environ.get("WATCH_CLANK_PACKAGED_REVISION", "local development build")

a = Analysis(
    [str(root / "native" / "macos" / "launcher.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / "app" / "templates"), "app/templates"),
        (str(root / "alembic"), "alembic"),
        (str(root / "alembic.ini"), "."),
    ],
    hiddenimports=["uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Watch Clank", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="Watch Clank")
app = BUNDLE(coll, name="Watch Clank.app", bundle_identifier="com.clank.watch.fieldtest", info_plist={"CFBundleDisplayName": "Watch Clank", "CFBundleShortVersionString": "0.1.0"})
