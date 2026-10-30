"""Verify the actual group lock rather than a recyclable operating-system PID."""
from pathlib import Path
from platform_runtime import msvcrt


def instance_locked(folder):
    path=Path(folder)/'instance.lock'
    try:stream=path.open('rb')
    except FileNotFoundError:return False
    except PermissionError:return True  # Unknown ownership: never launch a duplicate.
    with stream:
        try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:return True
        msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
    return False
