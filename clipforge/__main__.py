import os
import sys

import uvicorn

from clipforge.cli import main as cli_main

ARGS = sys.argv[1:]
if ARGS and ARGS[0] in ("serve", "web", "server"):
    BIND_HOST = os.getenv("CLIPFORGE_HOST", "127.0.0.1")
    BIND_PORT = int(os.getenv("CLIPFORGE_PORT", "8000"))
    print("ClipForge listening on port " + str(BIND_PORT))
    uvicorn.run("clipforge.api:app", host=BIND_HOST, port=BIND_PORT, log_level="info")
else:
    sys.exit(cli_main())
