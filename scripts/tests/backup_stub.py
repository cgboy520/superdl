"""Safe command doubles for backup shell plumbing, not an encryption/DB smoke."""

import json
import os
from pathlib import Path
import stat
import sys

command = Path(sys.argv[0]).name
args = sys.argv[1:]
record = {"command": command}
code = 0
# Never record argument/env values; even failure output remains metadata-only.
if any(os.environ[key] in arg for key in ("BACKUP_ENCRYPT_KEY", "PGURL") for arg in args):
    record["secret_in_argv"] = True
    code = 91
elif command == "pg_dump":
    if os.environ.get("PGDATABASE") != os.environ["PGURL"]:
        code = 92
    else:
        Path(args[args.index("-f") + 1]).write_text("synthetic dump")
elif command == "gpg":
    home = Path(os.environ["GNUPGHOME"])
    record["private_home"] = home.is_dir() and stat.S_IMODE(home.stat().st_mode) == 0o700
    record["loopback"] = "--pinentry-mode" in args and args[args.index("--pinentry-mode") + 1] == "loopback"
    record["stdin_fd"] = "--passphrase-fd" in args and args[args.index("--passphrase-fd") + 1] == "0"
    record["passphrase_received"] = sys.stdin.read().rstrip("\n") == os.environ["BACKUP_ENCRYPT_KEY"]
    if not all(record[k] for k in ("private_home", "loopback", "stdin_fd", "passphrase_received")):
        code = 93
    elif os.environ.get("BACKUP_STUB_GPG_FAIL") == "1":
        code = 1
    else:
        Path(args[args.index("-o") + 1]).write_text("synthetic output")
elif command == "pg_restore":
    record["exit_on_error"] = "--exit-on-error" in args
    if not record["exit_on_error"]:
        code = 94
elif command == "psql":
    print("0")
elif command not in ("gpg-agent", "initdb", "pg_ctl", "createdb"):
    code = 95
with Path(os.environ["BACKUP_TEST_LOG"]).open("a") as log:
    log.write(json.dumps(record) + "\n")
sys.exit(code)
