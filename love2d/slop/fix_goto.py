#!/usr/bin/env python3
"""Fix goto/label patterns for Lua 5.1 compatibility.

Converts:
  for ... do
    if cond then goto label end
    ...
    ::label::
  end

To:
  for ... do repeat
    if cond then break end
    ...
  until true end
"""

import re
import os

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, 'src')


def fix_file(filepath):
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()

    if 'goto ' not in content:
        return False

    lines = content.split('\n')
    changed = True
    iterations = 0

    while changed and iterations < 20:
        changed = False
        iterations += 1

        # Find all labels
        label_positions = {}
        for i, line in enumerate(lines):
            m = re.match(r'^(\s*)::(\w+)::\s*$', line)
            if m:
                label_positions[i] = m.group(2)

        # Find all gotos
        goto_positions = {}
        for i, line in enumerate(lines):
            m = re.search(r'\bgoto\s+(\w+)', line)
            if m:
                goto_positions[i] = m.group(1)

        if not goto_positions:
            break

        # For each goto, find matching label
        for goto_idx in sorted(goto_positions.keys()):
            label_name = goto_positions[goto_idx]

            # Find the label after this goto
            label_idx = None
            for li in sorted(label_positions.keys()):
                if li > goto_idx and label_positions[li] == label_name:
                    label_idx = li
                    break

            if label_idx is None:
                continue

            goto_line = lines[goto_idx]
            label_line = lines[label_idx]
            goto_indent = len(goto_line) - len(goto_line.lstrip())

            # Check if this is a loop-continue pattern:
            # ::label:: is followed by "end" (closing the loop)
            next_nonblank = label_idx + 1
            while next_nonblank < len(lines) and lines[next_nonblank].strip() == '':
                next_nonblank += 1

            is_loop_end = False
            if next_nonblank < len(lines):
                ns = lines[next_nonblank].strip()
                if ns == 'end' or ns.startswith('end ') or ns.startswith('end\t'):
                    is_loop_end = True

            if is_loop_end:
                # Find the matching loop start (for/while...do)
                # We need to count end/do nesting backwards from label_idx
                loop_start = None
                depth = 0
                for si in range(label_idx - 1, -1, -1):
                    s = lines[si].strip()
                    # Count block closers
                    if s == 'end' or s.startswith('end ') or s.startswith('end)') or s.startswith('end,'):
                        depth += 1
                    if re.match(r'^until\s', s) or s == 'until true':
                        depth += 1
                    # Count block openers
                    if re.search(r'\bdo\s*$', s) and (re.match(r'^\s*(for|while)\b', lines[si])):
                        if depth == 0:
                            loop_start = si
                            break
                        depth -= 1
                    elif s == 'repeat':
                        if depth == 0:
                            break
                        depth -= 1
                    elif re.match(r'^(if|elseif)\b.*\bthen\s*$', s):
                        if depth == 0:
                            break
                        depth -= 1
                    elif re.search(r'\bfunction\b', s) and not re.search(r'\bend\b', s):
                        if depth == 0:
                            break
                        depth -= 1

                if loop_start is not None:
                    # Transform: add "repeat" after loop "do" line
                    loop_indent_str = lines[loop_start][:len(lines[loop_start]) - len(lines[loop_start].lstrip())]

                    # Modify loop start: add " repeat" or next line
                    if lines[loop_start].rstrip().endswith(' do'):
                        lines[loop_start] = lines[loop_start].rstrip() + ' repeat'
                    else:
                        lines.insert(loop_start + 1, loop_indent_str + '    repeat')
                        # Adjust indices
                        goto_idx += 1
                        label_idx += 1
                        next_nonblank += 1

                    # Replace all gotos to this label within this loop with "break"
                    for gi in range(loop_start, label_idx):
                        lines[gi] = re.sub(r'\bgoto\s+' + re.escape(label_name) + r'\b', 'break', lines[gi])

                    # Replace ::label:: with "until true"
                    label_indent_str = label_line[:len(label_line) - len(label_line.lstrip())]
                    lines[label_idx] = label_indent_str + 'until true'

                    changed = True
                    break  # Restart processing after modification

            else:
                # Forward-skip goto: wrap skipped code in if-not-condition
                # Simple approach: replace "goto label" with if-then wrapping
                goto_stripped = goto_line.strip()

                # Check if it's "if cond then goto label end" pattern
                m_if = re.match(r'^(\s*)if\s+(.+?)\s+then\s+goto\s+' + re.escape(label_name) + r'\s+end\s*$', goto_line)
                if m_if:
                    indent = m_if.group(1)
                    cond = m_if.group(2)
                    lines[goto_idx] = indent + 'if not (' + cond + ') then'
                    label_indent_str = label_line[:len(label_line) - len(label_line.lstrip())]
                    lines[label_idx] = indent + 'end'
                    changed = True
                    break

                # Check if it's standalone "goto label"
                elif goto_stripped == 'goto ' + label_name:
                    # This skips everything until ::label::
                    # Wrap in "if false then ... end" or use a flag
                    indent_str = goto_line[:goto_indent]
                    lines[goto_idx] = indent_str + 'if false then'
                    label_indent_str = label_line[:len(label_line) - len(label_line.lstrip())]
                    lines[label_idx] = indent_str + 'end'
                    changed = True
                    break

                # Inline: "if cond then ... goto label end" (without end)
                elif 'goto ' + label_name in goto_line:
                    lines[goto_idx] = lines[goto_idx].replace('goto ' + label_name, 'break')
                    # Still need to handle the label
                    label_indent_str = label_line[:len(label_line) - len(label_line.lstrip())]
                    # Check if label is at end of loop
                    if is_loop_end:
                        lines[label_idx] = label_indent_str + 'until true'
                    else:
                        lines[label_idx] = label_indent_str + '-- label removed: ' + label_name
                    changed = True
                    break

    new_content = '\n'.join(lines)
    if new_content != content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return True
    return False


def find_lua_files(root):
    result = []
    for dirpath, dirnames, filenames in os.walk(root):
        for fn in filenames:
            if fn.endswith('.lua'):
                result.append(os.path.join(dirpath, fn))
    return result


if __name__ == '__main__':
    files = find_lua_files(SRC)
    fixed = 0
    for f in files:
        if 'port_shaders' in f:
            continue
        try:
            if fix_file(f):
                rel = os.path.relpath(f, BASE)
                print(f"Fixed: {rel}")
                fixed += 1
        except Exception as e:
            rel = os.path.relpath(f, BASE)
            print(f"ERROR in {rel}: {e}")

    print(f"\nFixed {fixed} files")
