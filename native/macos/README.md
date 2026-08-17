# Watch Clank macOS field-test launcher

`build.sh` creates the canonical Finder app at `dist/Watch Clank.app`. It forces loopback-only, read-only operation and stores every mutable artifact below `~/Library/Application Support/Watch Clank/`. Packaged configuration, templates, and Alembic migrations remain read-only bundle resources. Discord/webhook variables are stripped before the application is imported; collectors are never started automatically and all HTTP mutations return 403.
