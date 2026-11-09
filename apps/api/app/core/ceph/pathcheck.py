"""Standalone check run by the system interpreter (`/usr/bin/python3 -I`, package python3-cephfs):
mount one CephFS path in user space with the given client key and list it. Exit status 0 = the
key authenticates and can read the path, 2 = the path does not exist, 13 = permission denied,
1 = anything else; the reason goes to stderr. Never imported by the application."""

import argparse
import sys

import cephfs  # pyright: ignore[reportMissingImports]

MOUNT_TIMEOUT_SECONDS = "15"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--conf", required=True)
    parser.add_argument("--client", required=True)
    parser.add_argument("--fs", required=True)
    parser.add_argument("--path", required=True)
    args = parser.parse_args()
    fs = cephfs.LibCephFS(conffile=args.conf, auth_id=args.client)
    try:
        fs.conf_set("client_mount_timeout", MOUNT_TIMEOUT_SECONDS)
        fs.mount(mount_root=args.path.encode(), filesystem_name=args.fs.encode())
        handle = fs.opendir(b"/")
        fs.readdir(handle)
        fs.closedir(handle)
        fs.unmount()
    except cephfs.ObjectNotFound as exc:
        sys.stderr.write(f"path not found: {exc}\n")
        return 2
    except cephfs.PermissionError as exc:
        sys.stderr.write(f"permission denied: {exc}\n")
        return 13
    except cephfs.Error as exc:
        sys.stderr.write(f"mount failed: {exc}\n")
        return 1
    finally:
        fs.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
