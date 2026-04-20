#!/usr/bin/env python3

import argparse
import os
import shutil
import subprocess
import sys
import zipfile


HERE = os.path.dirname(os.path.abspath(__file__))
SLOP = os.path.join(HERE, "slop") # the ai slop python scripts
SRC = os.path.join(SLOP, "src")
OUT = os.path.join(HERE, "gome.love")


def extract(exe_path, dest):
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    with zipfile.ZipFile(exe_path, "r") as zf:
        zf.extractall(dest)


def run(script, *args):
    cmd = [sys.executable, os.path.join(SLOP, script), *args]
    print(f">>> {' '.join(cmd)}") # ts >>> so tuff hacker vibes right guys?
    subprocess.run(cmd, check=True, cwd=SLOP)


def repack(src, out):
    if os.path.exists(out):
        os.remove(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, _, filenames in os.walk(src):
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, src)
                zf.write(full, rel)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("exe", help="path to love2d executable")
    ap.add_argument("--keep", action="store_true", help="keep source folder after porting")
    args = ap.parse_args()

    if not os.path.isfile(args.exe):
        sys.exit(f"not a file: {args.exe}")

    print(f"extracting {args.exe} -> {SRC}")
    extract(args.exe, SRC)

    run("fix_goto.py")
    run("port_shaders.py")
    run("minify_lua.py", SRC)

    print(f"zipping: {OUT}")
    repack(SRC, OUT)

    if not args.keep:
        shutil.rmtree(SRC)

    print(f"done: {OUT}")


if __name__ == "__main__":
    main()
