"""Image-build step only: remove executable elevation bits and file capabilities from the rootfs."""
import os
from pathlib import Path
import stat

if os.getuid() != 0 or not Path("/etc/altitude/container").is_file():
    raise SystemExit("Only run this step inside the new Altitude image")
for base, directories, filenames in os.walk("/", followlinks=False):
    if base == "/":
        directories[:] = [name for name in directories if name not in {"proc", "sys", "dev", "run"}]
    for name in filenames:
        path = Path(base) / name
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            continue
        if info.st_mode & 0o6000:
            path.chmod(info.st_mode & ~0o6000)
        if "security.capability" in os.listxattr(path):
            os.removexattr(path, "security.capability")
