#!/usr/bin/env python3
"""
Ports LÖVE GLSL3 shaders to GLSL ES 1.0 (WebGL1) for love.js compat mode.

Transformations applied:
  1. Remove #pragma language glsl3/glsl4
  2. Convert const array initializers → lookup functions
  3. Convert switch/case/break → if/else chains
  4. Convert bitwise ops (<<, >>, &, |) → math equivalents
  5. Convert hex literals → decimal
  6. Convert int modulo (%) → mod()
  7. Convert ^^ (logical XOR) → !=
  8. Rename variables that shadow GLSL builtins (e.g. 'step')
  9. Strip uniform array initializers (LÖVE sends defaults from CPU)
 10. Strip uniform default values (GLSL ES 1.0 forbids initializers)
 11. Convert uniform int → float when used in float arithmetic
"""

import re
import os
import sys
import copy

SHADER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src", "assets", "shaders")
DECO_SHADER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src", "obj", "DecoShader.lua")
SKIP_FILES = {"darkness.glsl", "noisetexture.png"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def hex_to_dec(m):
    """Regex replacer: convert 0xABC to decimal."""
    return str(int(m.group(0), 16))


def parse_toplevel_csv(s):
    """Split by commas at depth-0 only, respecting nested parens."""
    # First strip all inline comments
    s = re.sub(r"//[^\n]*", "", s)
    items, depth, cur = [], 0, []
    for ch in s:
        if ch == "(":
            depth += 1; cur.append(ch)
        elif ch == ")":
            depth -= 1; cur.append(ch)
        elif ch == "," and depth == 0:
            items.append("".join(cur).strip()); cur = []
        else:
            cur.append(ch)
    if cur:
        items.append("".join(cur).strip())
    return [v.strip() for v in items if v.strip()]


def find_balanced(code, start, open_ch, close_ch):
    """Return index after the matching close_ch, starting at code[start]==open_ch."""
    assert code[start] == open_ch, f"Expected '{open_ch}' at pos {start}, got '{code[start]}'"
    depth, i = 0, start
    while i < len(code):
        if code[i] == open_ch:
            depth += 1
        elif code[i] == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(code) - 1

# ---------------------------------------------------------------------------
# 1. Remove #pragma
# ---------------------------------------------------------------------------

def remove_pragma(code):
    return re.sub(r"#pragma\s+language\s+glsl\d+\s*\n?", "", code)

# ---------------------------------------------------------------------------
# 2. Convert const array declarations → lookup functions
# ---------------------------------------------------------------------------

def const_array_to_func(code):
    """Replace `const T name[N] = T[N](...);` with a lookup function."""
    converted = set()
    header_re = re.compile(r"const\s+(\w+)\s+(\w+)\[(\d+)\]\s*=\s*\w+\[\d+\]\(")

    while True:
        m = header_re.search(code)
        if not m:
            break
        typ = m.group(1)
        name = m.group(2)
        # find the opening '(' at end of match, then balance to closing ')'
        paren_open = m.end() - 1  # the '(' at end of header
        paren_close = find_balanced(code, paren_open, "(", ")")
        # skip to the ';'
        semi = code.index(";", paren_close)
        values_str = code[paren_open + 1 : paren_close]
        values = parse_toplevel_csv(values_str)
        converted.add(name)
        lines = [f"{typ} _get_{name}(int i) {{"]
        for idx, val in enumerate(values):
            lines.append(f"    if (i == {idx}) return {val};")
        lines.append(f"    return {values[0]};")
        lines.append("}")
        code = code[: m.start()] + "\n".join(lines) + code[semi + 1 :]

    # Replace array indexing → function call for converted arrays
    for name in converted:
        code = re.sub(
            rf"(?<!\w){re.escape(name)}\[([^\]]+)\]",
            rf"_get_{name}(\1)",
            code,
        )
    return code

# ---------------------------------------------------------------------------
# 3. Strip uniform array initializers  (LÖVE handles defaults on CPU side)
# ---------------------------------------------------------------------------

def strip_uniform_array_init(code):
    header_re = re.compile(r"(uniform\s+\w+\s+\w+\[\d+\])\s*=\s*\w+\[\d+\]\(")
    while True:
        m = header_re.search(code)
        if not m:
            break
        paren_open = m.end() - 1
        paren_close = find_balanced(code, paren_open, "(", ")")
        semi = code.index(";", paren_close)
        decl = m.group(1)
        code = code[: m.start()] + decl + ";" + code[semi + 1 :]
    return code

# ---------------------------------------------------------------------------
# 4. Convert switch/case → if/else
# ---------------------------------------------------------------------------

def convert_switch(code):
    """Convert switch(expr){case N: ...break; ...} → if/else chains."""
    while True:
        m = re.search(r"\bswitch\s*\(", code)
        if not m:
            break
        # find the expression in parens
        paren_open = code.index("(", m.start())
        paren_close = find_balanced(code, paren_open, "(", ")")
        expr = code[paren_open + 1 : paren_close].strip()

        # find the body in braces
        brace_open = code.index("{", paren_close)
        brace_close = find_balanced(code, brace_open, "{", "}")
        body = code[brace_open + 1 : brace_close]

        # parse cases
        parts = re.split(r"\b(case\s+\w+|default)\s*:", body)
        # parts[0] is stuff before first case (whitespace), then alternating label/body
        cases = []
        i = 1
        while i < len(parts):
            label = parts[i].strip()
            body_text = parts[i + 1] if i + 1 < len(parts) else ""
            # strip trailing break;
            body_text = re.sub(r"\bbreak\s*;\s*$", "", body_text.strip()).strip()
            if label.startswith("case"):
                val = label.split()[1]
                cases.append((val, body_text))
            else:
                cases.append(("default", body_text))
            i += 2

        # build if/else
        out_parts = []
        for idx, (val, stmts) in enumerate(cases):
            if val == "default":
                out_parts.append(f" else {{\n\t\t{stmts}\n\t}}")
            elif idx == 0:
                out_parts.append(f"if ({expr} == {val}) {{\n\t\t{stmts}\n\t}}")
            else:
                out_parts.append(f" else if ({expr} == {val}) {{\n\t\t{stmts}\n\t}}")

        replacement = "".join(out_parts)
        code = code[: m.start()] + replacement + code[brace_close + 1 :]
    return code

# ---------------------------------------------------------------------------
# 5. Convert hex literals → decimal
# ---------------------------------------------------------------------------

def convert_hex(code):
    return re.sub(r"\b0x[0-9A-Fa-f]+\b", hex_to_dec, code)

# ---------------------------------------------------------------------------
# 6. Convert bitwise operations → math
# ---------------------------------------------------------------------------

def convert_bitwise(code):
    # First handle compound pattern: (expr & MASK) >> SHIFT
    # This is bit-field extraction: extract `width` bits starting at bit `shift`
    # Result: int(mod(floor(float(expr) / 2^shift), 2^width))
    def compound_and_shift(m):
        expr = m.group(1).strip()
        mask_s = m.group(2).strip()
        shift_s = m.group(3).strip()
        try:
            mask = int(mask_s)
            shift = int(shift_s)
            divisor = 1 << shift
            extracted = mask >> shift
            modulus = extracted + 1
            if shift == 0:
                return f"int(mod(float({expr}), {float(modulus)}))"
            return f"int(mod(floor(float({expr}) / {float(divisor)}), {float(modulus)}))"
        except ValueError:
            return m.group(0)  # leave as-is

    code = re.sub(
        r"\((\w+)\s*&\s*([\w]+)\)\s*>>\s*([\w]+)",
        compound_and_shift,
        code,
    )

    # a << N  →  a * 2^N
    def shift_left(m):
        a, b = m.group(1).strip(), m.group(2).strip()
        try:
            n = int(b)
            return f"{a} * {1 << n}"
        except ValueError:
            return f"int(float({a}) * exp2(float({b})))"

    # a >> N  →  int(floor(float(a) / 2^N))
    def shift_right(m):
        a, b = m.group(1).strip(), m.group(2).strip()
        try:
            n = int(b)
            if n == 0:
                return a
            return f"int(floor(float({a}) / {float(1 << n)}))"
        except ValueError:
            return f"int(floor(float({a}) / exp2(float({b}))))"

    code = re.sub(r"([\w.()]+)\s*>>\s*([\w.()]+)", shift_right, code)
    code = re.sub(r"([\w.()]+)\s*<<\s*([\w.()]+)", shift_left, code)

    # expr & mask  →  int(mod(float(expr), float(mask + 1)))
    def bitwise_and(m):
        a, b = m.group(1).strip(), m.group(2).strip()
        try:
            mask = int(b)
            return f"int(mod(float({a}), {float(mask + 1)}))"
        except ValueError:
            return m.group(0)  # leave as-is if not a literal

    code = re.sub(r"(\w+)\s*&\s*(\d+)", bitwise_and, code)

    return code

# ---------------------------------------------------------------------------
# 7. Convert int modulo → mod()
# ---------------------------------------------------------------------------

def convert_int_modulo(code):
    # Pattern: int(expr) % N  or  expr % N where expr is int context
    # Convert to: int(mod(float(expr), float(N)))
    # Handle: (int(fc.x) % 16)
    def modulo_replace(m):
        a = m.group(1).strip()
        b = m.group(2).strip()
        try:
            n = int(b)
            return f"int(mod(float({a}), {float(n)}))"
        except ValueError:
            try:
                f = float(b)
                return f"int(mod(float({a}), {b}))"
            except ValueError:
                return f"int(mod(float({a}), float({b})))"

    # Only match integer modulo (% with non-float operands)
    # Avoid matching inside comments
    code = re.sub(r"([\w.()]+)\s*%\s*([\w.()]+)", modulo_replace, code)
    return code

# ---------------------------------------------------------------------------
# 8. Convert ^^ (logical XOR) → !=
# ---------------------------------------------------------------------------

def convert_xor(code):
    return code.replace("^^", "!=")

# ---------------------------------------------------------------------------
# 9. Rename variables shadowing GLSL builtins
# ---------------------------------------------------------------------------

BUILTIN_CONFLICTS = {
    "step": "pxStep",
}

def rename_builtin_shadows(code):
    for old, new in BUILTIN_CONFLICTS.items():
        # Only rename local variable declarations and usages, not function calls
        # Pattern: type step = ... or step.x etc  (but NOT step(...) function call)
        # Look for declarations like: vec2 step =
        if re.search(rf"\b(?:vec2|vec3|vec4|float|int)\s+{old}\b", code):
            # Rename all occurrences of `step` that aren't function calls
            # step(...) should stay, step.x or step; should be renamed
            code = re.sub(rf"\b{old}(?=\s*[;=.,\[\)\s])", new, code)
            # Fix any declarations
            code = re.sub(rf"\b{old}(?=\s*[;=])", new, code)
    return code

# ---------------------------------------------------------------------------
# 10. Convert ivec4 to int(floor(...)) based operations
# ---------------------------------------------------------------------------

def convert_ivec(code):
    # ivec4(expr) → just use the float values since we converted bitwise ops
    # Replace ivec4 declarations: ivec4 name = ivec4(expr)  →  vec4 name = floor(expr)
    code = re.sub(
        r"\bivec4\s+(\w+)\s*=\s*ivec4\(([^)]+)\)",
        r"vec4 \1 = floor(\2)",
        code,
    )
    code = re.sub(
        r"\bivec3\s+(\w+)\s*=\s*ivec3\(([^)]+)\)",
        r"vec3 \1 = floor(\2)",
        code,
    )
    # ivec4(expr) in expressions → floor(expr) cast
    code = re.sub(r"\bivec4\(([^)]+)\)", r"floor(\1)", code)
    code = re.sub(r"\bivec3\(([^)]+)\)", r"floor(\1)", code)
    return code

# ---------------------------------------------------------------------------
# 11. Convert bare int literals to float in vec constructors
# ---------------------------------------------------------------------------

def convert_int_literals_in_vecs(code):
    """Convert `vec2(0, expr)` → `vec2(0.0, expr)` etc.
    GLSL ES 1.0 requires float args in vec constructors."""
    def fix_vec_args(m):
        prefix = m.group(1)  # e.g. "vec4("
        args_str = m.group(2)
        # Split respecting nested parens
        args = parse_toplevel_csv(args_str)
        fixed = []
        for arg in args:
            # If arg is a bare integer literal (no decimal point), convert to float
            if re.match(r"^-?\d+$", arg.strip()):
                fixed.append(arg.strip() + ".0")
            else:
                fixed.append(arg)
        return prefix + ",".join(fixed) + ")"

    # Match vec2(...), vec3(...), vec4(...) — non-greedy, balanced parens
    def replace_vec(code):
        pattern = re.compile(r"(vec[234]\s*)\(")
        result = []
        last = 0
        for m in pattern.finditer(code):
            paren_open = m.end() - 1
            paren_close = find_balanced(code, paren_open, "(", ")")
            args_str = code[paren_open + 1 : paren_close]
            args = parse_toplevel_csv(args_str)
            fixed = []
            for arg in args:
                if re.match(r"^-?\d+$", arg.strip()):
                    fixed.append(arg.strip() + ".0")
                else:
                    fixed.append(arg)
            result.append(code[last:m.start()])
            result.append(m.group(1) + "(" + ",".join(fixed) + ")")
            last = paren_close + 1
        result.append(code[last:])
        return "".join(result)

    return replace_vec(code)

# ---------------------------------------------------------------------------
# 12. Strip uniform initializers (GLSL ES 1.0 forbids `uniform T x = val;`)
# ---------------------------------------------------------------------------

def strip_uniform_init(code):
    """Remove default values from uniform declarations.
    `uniform float x = 1.0;` → `uniform float x;`
    Does NOT touch uniform arrays (handled by strip_uniform_array_init).
    """
    # Match: uniform <type> <name> = <value>;
    # But not: uniform <type> <name>[...] (arrays)
    code = re.sub(
        r"(uniform\s+\w+\s+\w+)\s*=\s*[^;]+;",
        r"\1;",
        code,
    )
    return code

# ---------------------------------------------------------------------------
# 12. Convert uniform int → float when used in float arithmetic
# ---------------------------------------------------------------------------

def convert_uniform_int_to_float(code):
    """Change `uniform int name` to `uniform float name` when the variable
    appears in float expressions (multiplied/divided/added with floats).
    GLSL ES 1.0 has no implicit int→float conversion.

    Heuristic: if `name` ever appears next to a float operator context
    (e.g. `* name`, `name *`, `/ name`, `name +` with float operands,
    or inside float-returning functions like floor(), mod(), etc.),
    promote to float.

    Conservative approach: promote gameWidth, gameHeight always since they're
    almost always used in float math. For others, check usage context.
    """
    # Find all `uniform int <name>` declarations (not arrays)
    decl_re = re.compile(r"uniform\s+int\s+(\w+)\s*;")
    int_uniforms = decl_re.findall(code)

    for name in int_uniforms:
        # Check if this int uniform is used in a float context:
        # - multiplied/divided with a float expression (contains '.')
        # - used as argument to float functions (floor, mod, sin, cos, etc.)
        # - multiplied with texture_coords, screen_coords, or other vec/float vars
        # - appears in expressions with float literals (N.N)
        float_context = False

        # Check for: <float_expr> * name, name * <float_expr>, etc.
        # Look for patterns where name is adjacent to float operations
        patterns = [
            rf"[\d.]+\s*\*\s*{name}\b",      # 2.0 * name
            rf"\b{name}\s*\*\s*[\d.]*\.",     # name * 2.0  (has decimal point)
            rf"\b{name}\s*[+\-*/]\s*\w+\.\w", # name + foo.x  (swizzle = vec = float)
            rf"\w+\.\w\s*[+\-*/]\s*{name}\b", # foo.x + name
            rf"float\s*\(\s*{name}\s*\)",      # float(name) - explicit cast means it's used as float
            rf"floor\s*\([^)]*{name}",         # floor(... name ...)
            rf"mod\s*\([^)]*{name}",           # mod(... name ...)
            rf"texture_coords\s*\.\w\s*\*\s*{name}", # texture_coords.x * name
            rf"{name}\s*\*\s*texture_coords",  # name * texture_coords
            rf"screen_coords\s*\.\w\s*\*\s*{name}",
        ]
        for pat in patterns:
            if re.search(pat, code):
                float_context = True
                break

        if float_context:
            code = re.sub(
                rf"uniform\s+int\s+{name}\s*;",
                f"uniform float {name};",
                code,
            )

    return code

# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def port_shader(code, filename=""):
    """Apply all transformations to a GLSL shader."""
    code = remove_pragma(code)
    code = const_array_to_func(code)
    code = strip_uniform_array_init(code)
    code = convert_switch(code)
    code = convert_hex(code)
    code = convert_bitwise(code)
    code = convert_int_modulo(code)
    code = convert_xor(code)
    code = rename_builtin_shadows(code)
    code = convert_ivec(code)
    code = convert_int_literals_in_vecs(code)
    code = strip_uniform_init(code)
    code = convert_uniform_int_to_float(code)
    return code


def port_decoshader_lua(path):
    """Port the DecoShader.lua template string."""
    with open(path, "r") as f:
        lua = f.read()

    # Extract the template between [[ and ]]
    m = re.search(r"(DecoShader\.static\.template\s*=\s*\[\[)(.*?)(\]\])", lua, re.DOTALL)
    if not m:
        print(f"  WARNING: Could not find template in {path}")
        return

    before = m.group(1)
    template = m.group(2)
    after = m.group(3)

    ported = port_shader(template, "DecoShader.lua")

    new_lua = lua[: m.start()] + before + ported + after + lua[m.end() :]
    with open(path, "w") as f:
        f.write(new_lua)
    print(f"  Ported DecoShader.lua template")


def main():
    shader_dir = SHADER_DIR
    if len(sys.argv) > 1:
        shader_dir = sys.argv[1]

    print(f"Porting shaders in: {shader_dir}")
    print()

    files = sorted(os.listdir(shader_dir))
    for fn in files:
        if fn in SKIP_FILES:
            print(f"  SKIP  {fn}")
            continue
        if not fn.endswith(".glsl"):
            continue

        path = os.path.join(shader_dir, fn)
        with open(path, "r") as f:
            original = f.read()

        ported = port_shader(original, fn)

        if ported != original:
            with open(path, "w") as f:
                f.write(ported)
            print(f"  PORT  {fn}")
        else:
            print(f"  OK    {fn}")

    # Also port the DecoShader.lua template
    print()
    if os.path.exists(DECO_SHADER):
        port_decoshader_lua(DECO_SHADER)
    else:
        print(f"  DecoShader.lua not found at {DECO_SHADER}")

    print()
    print("Done. Review the output shaders for correctness.")
    print("Shaders with heavy bitwise/switch usage (ontop.glsl) should be manually verified.")


if __name__ == "__main__":
    main()
