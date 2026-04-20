#!/usr/bin/env python3
"""Minify all .lua files in the directory tree."""

import os
import re
import sys
import argparse

def minify_lua(source: str) -> str:
    """Minify Lua source code by removing comments and unnecessary whitespace."""
    result = []
    i = 0
    n = len(source)

    while i < n:
        # Long string / long comment: --[=*[ ... ]=*] or [=*[ ... ]=*]
        if source[i:i+2] == '--' and i + 2 < n and source[i+2] == '[':
            # Check for long comment --[=*[
            j = i + 3
            eq_count = 0
            while j < n and source[j] == '=':
                eq_count += 1
                j += 1
            if j < n and source[j] == '[':
                # Long comment, skip until ]=*]
                close = ']' + '=' * eq_count + ']'
                end = source.find(close, j + 1)
                if end == -1:
                    i = n
                else:
                    i = end + len(close)
                continue
            # Not a long comment, fall through to short comment

        # Short comment: -- ...
        if source[i:i+2] == '--':
            # Skip to end of line
            while i < n and source[i] != '\n':
                i += 1
            continue

        # Long string literal: [=*[ ... ]=*]
        if source[i] == '[' and i + 1 < n and source[i+1] in '[=':
            j = i + 1
            eq_count = 0
            while j < n and source[j] == '=':
                eq_count += 1
                j += 1
            if j < n and source[j] == '[':
                close = ']' + '=' * eq_count + ']'
                end = source.find(close, j + 1)
                if end == -1:
                    result.append(source[i:])
                    i = n
                else:
                    result.append(source[i:end + len(close)])
                    i = end + len(close)
                continue

        # String literals
        if source[i] in ('"', "'"):
            quote = source[i]
            result.append(quote)
            i += 1
            while i < n and source[i] != quote:
                if source[i] == '\\' and i + 1 < n:
                    result.append(source[i:i+2])
                    i += 2
                else:
                    result.append(source[i])
                    i += 1
            if i < n:
                result.append(source[i])
                i += 1
            continue

        # Whitespace: collapse runs of whitespace
        if source[i] in ' \t\r\n':
            # Collect all whitespace
            had_newline = False
            while i < n and source[i] in ' \t\r\n':
                if source[i] == '\n':
                    had_newline = True
                i += 1
            # We need whitespace between identifiers/keywords/numbers
            # Check if previous and next chars are alphanumeric/underscore
            prev_alnum = len(result) > 0 and (result[-1][-1:].isalnum() or result[-1][-1:] in ('_', '.'))
            next_alnum = i < n and (source[i].isalnum() or source[i] == '_')
            if prev_alnum and next_alnum:
                result.append(' ')
            continue

        result.append(source[i])
        i += 1

    return ''.join(result)


def main():
    parser = argparse.ArgumentParser(description='Minify .lua files')
    parser.add_argument('path', nargs='?', default='.', help='Root directory to search (default: current dir)')
    parser.add_argument('--suffix', default='.lua', help='Output suffix (default: overwrite in place)')
    parser.add_argument('--outdir', help='Output directory (preserves structure). If not set, overwrites in place.')
    parser.add_argument('--dry-run', action='store_true', help='Print stats without writing')
    parser.add_argument('--exclude', nargs='*', default=[], help='Directory names to exclude')
    args = parser.parse_args()

    root = os.path.abspath(args.path)
    total_original = 0
    total_minified = 0
    file_count = 0

    for dirpath, dirnames, filenames in os.walk(root):
        # Exclude directories
        dirnames[:] = [d for d in dirnames if d not in args.exclude]

        for fname in filenames:
            if not fname.endswith('.lua'):
                continue

            filepath = os.path.join(dirpath, fname)
            try:
                with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                    original = f.read()
            except (IOError, OSError) as e:
                print(f"  SKIP {filepath}: {e}", file=sys.stderr)
                continue

            minified = minify_lua(original)
            orig_size = len(original.encode('utf-8'))
            mini_size = len(minified.encode('utf-8'))
            total_original += orig_size
            total_minified += mini_size
            file_count += 1

            rel = os.path.relpath(filepath, root)
            ratio = (1 - mini_size / orig_size) * 100 if orig_size > 0 else 0
            print(f"  {rel}: {orig_size} -> {mini_size} bytes ({ratio:.1f}% reduction)")

            if not args.dry_run:
                if args.outdir:
                    outpath = os.path.join(args.outdir, rel)
                    os.makedirs(os.path.dirname(outpath), exist_ok=True)
                else:
                    outpath = filepath

                with open(outpath, 'w', encoding='utf-8') as f:
                    f.write(minified)

    if file_count == 0:
        print("No .lua files found.")
    else:
        ratio = (1 - total_minified / total_original) * 100 if total_original > 0 else 0
        print(f"\n  {file_count} files: {total_original} -> {total_minified} bytes ({ratio:.1f}% total reduction)")
        if args.dry_run:
            print("  (dry run — no files modified)")


if __name__ == '__main__':
    main()
