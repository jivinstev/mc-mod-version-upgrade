"""Single-shot workers for tools/file-loop.py: the script gathers what a fix needs, the model answers once.

Why (measured, docs/EVALS.md step 6/7 spike): an agent worker carries ~30k tokens of harness context on
every request and spent ~20 requests per batch searching game sources and reading files. Here the
SCRIPT does the searching -- the file, its errors, the catalogue entries they match, and the real
declarations of the game types the errors name -- and the model, with a ~1k-token system prompt and no
tools, returns exact SEARCH/REPLACE edits in one request.

Correctness is enforced here, never trusted:
  * a SEARCH block must match the file exactly once, or the whole answer is rejected (no partial edits);
  * edits may only touch the worker's own file;
  * the GUARD rejects an answer that deletes a large share of the file, comments code out, adds an
    UnsupportedOperationException, or empties method bodies -- the ways to make an error vanish without
    fixing it;
  * the compiler (file-loop) and the gates still judge the result; a file that fails here escalates to
    the agent workers, so this mode can only add a cheaper first attempt, never lower the floor.
Standard library only.
"""
import collections, difflib, json, os, pathlib, re, subprocess, time

SYSTEM = ("You fix Java compile errors in a Minecraft mod ported to a newer Minecraft/NeoForge. You get one "
          "file, its errors, catalogue notes and the real declarations of the game types involved. Reply ONLY "
          "with SEARCH/REPLACE blocks and the two closing lines asked for. Keep behaviour; never delete or stub "
          "logic to silence an error.")

PROMPT = """Target: {target}. Fix the compile errors in {rel}.

ERRORS ({n}):
{errors}

CATALOGUE NOTES (old shape -> fix):
{entries}

GAME DECLARATIONS (from the target's own sources; trust these over memory):
{api}

FILE {rel} (line-numbered{partial}):
{text}

Answer with one or more blocks, each exactly:
<<<<<<< SEARCH
<lines copied EXACTLY from the file, without the line numbers, enough to be unique>
=======
<the replacement lines>
>>>>>>> REPLACE
Then two lines:
NEEDS: <a change another file needs, or none>
RULE: <one rename-table row `from<TAB>to` if this exact edit applies everywhere, else none>"""

BLOCK = re.compile(r'<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n?>>>>>>> REPLACE', re.S)
DECL = re.compile(r'\b(?:class|interface|record|enum)\s+(\w+)[^{;]*\{')
MEMBER = re.compile(r'^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:(?:public|protected|static|final|abstract|default)\s+)+'
                    r'(?:<[^>]+>\s+)?[\w.<>\[\], ?]+\s+(\w+)\s*(\([^;{]*\)|\s*[=;])', re.M)
IDENT = re.compile(r'\b([A-Z][A-Za-z0-9_]*)\b')


COMMENTS = re.compile(r'/\*.*?\*/|//[^\n]*', re.S)
NESTED = re.compile(r'\b(?:class|interface|record|enum)\s+(\w+)')
JAVA_LANG = set("""String Object Integer Long Double Float Boolean Character Byte Short Math Class Enum Record
Iterable Runnable Thread Exception RuntimeException Throwable Error Void Number System StringBuilder
Comparable CharSequence Override Deprecated FunctionalInterface SuppressWarnings T E K V R""".split())
JAVAC = set("""cannot find symbol method variable class package does not exist incompatible types be converted to
is not abstract and override required found reason location the of in for has private access protected expected
argument arguments no suitable constructor interface enum record type""".split())


class SourceIndex:
    """Simple-name index of the target's game sources -- top-level AND nested types -- answering: which
    class is called X, and what does it declare."""

    def __init__(self, root):
        self.files = collections.defaultdict(list)
        if root and pathlib.Path(root).is_dir():
            for f in pathlib.Path(root).rglob("*.java"):
                self.files[f.stem].append(f)
                for n in set(NESTED.findall(COMMENTS.sub("", f.read_text(encoding="utf-8", errors="replace")))):
                    if n != f.stem:
                        self.files[n].append(f)
        self.names = sorted(self.files)
        self.cache = {}

    def pick(self, cls, prefer=()):
        """The file declaring cls: a top-level class of that name first, else a nested one inside a class
        the caller names (AttributeModifier.Operation, not some other Operation), else the first."""
        fs = self.files.get(cls, [])
        return ([f for f in fs if f.stem == cls] + [f for f in fs if f.stem in prefer] + fs)[:1]

    def members(self, cls, prefer=()):
        """-> [(name, one-line signature)]: the declaration of cls, then its public/protected members."""
        key = (cls, tuple(sorted(prefer)))
        if key in self.cache:
            return self.cache[key]
        out = []
        for f in self.pick(cls, prefer):
            text = COMMENTS.sub("", f.read_text(encoding="utf-8", errors="replace"))
            head = re.search(rf'\b(?:class|interface|record|enum)\s+{re.escape(cls)}\b[^{{;]*\{{', text)
            pkg = re.search(r'^package ([\w.]+);', text, re.M)
            hd = re.sub(r'\s+', ' ', head.group(0))[:220] if head else cls
            out.append(("", (pkg.group(1) + "." if pkg else "") + f.stem + (": " if f.stem == cls else f" (nested): ") + hd))
            if head and re.search(r'\benum\b', head.group(0)):   # an enum's constants come first, up to `;`
                body = text[head.end():text.find(";", head.end())]
                consts = re.findall(r'(?:^|,)\s*([A-Z][A-Z0-9_]*)\b', re.sub(r'\([^()]*\)', '', body))
                if consts:
                    out.append(("", "    constants: " + ", ".join(consts[:40])))
            for m in MEMBER.finditer(text):
                out.append((m.group(1), re.sub(r'\s+', ' ', m.group(0).strip())[:180]))
        self.cache[key] = out
        return out


def api_facts(idx, file_text, msgs, cap=70):
    """The declarations the errors are about: the game classes they name, their members the errors use
    (or close to them -- the rename case), and the nearest class names for one that is gone."""
    joined = " ".join(msgs)
    words = set(re.findall(r'[A-Za-z_]\w*', joined)) - JAVAC
    wanted = set(re.findall(r'(?:method|variable)\s+(\w+)', joined)) | set(re.findall(r'\b([a-z]\w*)\(', joined))
    java_imports = set(re.findall(r'^import java[\w.]*\.(\w+);', file_text, re.M)) | JAVA_LANG
    types = [w for w in sorted(words) if w[:1].isupper() and w not in java_imports and len(w) > 1]
    outers = set(re.findall(r'\b([A-Z]\w*)\.[A-Z]\w*', joined + " " + file_text))   # Outer.Inner in the code
    out, lines = [], 0
    for t in types[:12]:
        mem = idx.members(t, outers)
        if not mem:
            close = difflib.get_close_matches(t, idx.names, n=4, cutoff=0.75)
            out.append(f"- {t}: NOT in the target's sources (gone or renamed); closest: {', '.join(close) or 'none'}")
            lines += 1
            continue
        out.append(f"- {mem[0][1]}")
        out += [s for n, s in mem[1:] if n == ""]   # an enum's constants
        names = sorted({n for n, _s in mem[1:] if n})
        show = [s for n, s in mem[1:] if n in wanted]
        for w in wanted - set(names):   # a member the code calls that this type no longer has: show the nearest
            close = set(difflib.get_close_matches(w, names, n=2, cutoff=0.6))
            show += [s for n, s in mem[1:] if n in close]
        for s in list(dict.fromkeys(show))[:12]:
            out.append(f"    {s}")
        lines += 1 + min(12, len(show))
        if lines > cap:
            break
    return "\n".join(out) or "(none found)"


def numbered(text, err_lines, full_max=450, pad=25):
    lines = text.split("\n")
    if len(lines) <= full_max:
        return "\n".join(f"{i + 1:5}| {l}" for i, l in enumerate(lines)), ""
    keep = set(range(0, min(40, len(lines))))   # package, imports, class header
    for e in err_lines:
        keep |= set(range(max(0, e - 1 - pad), min(len(lines), e + pad)))
    out, prev = [], -1
    for i in sorted(keep):
        if i != prev + 1:
            out.append("   ...| (lines omitted)")
        out.append(f"{i + 1:5}| {lines[i]}"); prev = i
    return "\n".join(out), ", only the parts around the errors"


def parse_blocks(answer):
    return [(s, r) for s, r in BLOCK.findall(answer.replace("\r\n", "\n"))]


def _strip_numbers(s):
    return re.sub(r'^\s*\d+\| ?', '', s, flags=re.M) if re.search(r'^\s*\d+\| ', s, re.M) else s


def _loose_replace(text, s, r):
    """Whitespace-insensitive fallback: match the SEARCH lines with each line stripped, uniquely, then
    re-indent the replacement by the difference between the model's indent and the file's."""
    lines = text.split("\n")
    want = [l.strip() for l in s.split("\n") if l.strip()]
    if not want:
        return None, 0
    hits = []
    for i in range(len(lines)):
        j, k = i, 0
        while j < len(lines) and k < len(want):
            if not lines[j].strip():
                j += 1; continue
            if lines[j].strip() != want[k]:
                break
            j += 1; k += 1
        if k == len(want) and lines[i].strip() == want[0]:
            hits.append((i, j))
    if len(hits) != 1:
        return None, len(hits)
    i, j = hits[0]
    first_model = next(l for l in s.split("\n") if l.strip())
    shift = (len(lines[i]) - len(lines[i].lstrip())) - (len(first_model) - len(first_model.lstrip()))
    def indent(l):
        if not l.strip():
            return l
        if shift >= 0:
            return " " * shift + l
        cut = min(-shift, len(l) - len(l.lstrip()))
        return l[cut:]
    return "\n".join(lines[:i] + [indent(l) for l in r.split("\n")] + lines[j:]), 1


def apply_blocks(text, blocks):
    """-> (new text, None) or (None, why). All-or-nothing; each SEARCH must occur exactly once, exactly
    or -- failing that -- with leading/trailing whitespace ignored line by line."""
    if not blocks:
        return None, "no SEARCH/REPLACE blocks"
    new = text
    for s, r in blocks:
        s, r = _strip_numbers(s), _strip_numbers(r)
        n = new.count(s)
        if n == 1:
            new = new.replace(s, r, 1)
            continue
        loose, m = _loose_replace(new, s, r)
        if loose is None:
            return None, f"a SEARCH block matched {n} times exactly and {m} times ignoring whitespace"
        new = loose
    return new, None


def guard(before, after):
    """-> None when the edit looks like a fix, else why it looks like hiding the error."""
    b, a = before.split("\n"), after.split("\n")
    sm = difflib.SequenceMatcher(None, b, a)
    removed = sum(i2 - i1 for op, i1, i2, _j1, _j2 in sm.get_opcodes() if op in ("delete", "replace"))
    added_lines = [l for op, _i1, _i2, j1, j2 in sm.get_opcodes() if op in ("insert", "replace") for l in a[j1:j2]]
    if removed - len(added_lines) > max(30, len(b) // 4):
        return f"deletes {removed - len(added_lines)} net lines of {len(b)}"
    commented = [l for l in added_lines if re.match(r'\s*//.*[;{(]', l) and not re.match(r'\s*//\s*(NOTE|1\.21|MIGRATION)', l)]
    if len(commented) > 2:
        return f"comments code out ({len(commented)} lines)"
    if any("UnsupportedOperationException" in l for l in added_lines) and "UnsupportedOperationException" not in before:
        return "adds an UnsupportedOperationException"
    emptied = len(re.findall(r'\)\s*\{\s*\}', after)) - len(re.findall(r'\)\s*\{\s*\}', before))
    if emptied > 1:
        return f"empties {emptied} method bodies"
    return None


def run_single(work, f, errs_of_f, entry_text, idx, model_id, target, timeout=300, thinking=0):
    """One request: context in, SEARCH/REPLACE out, applied only if it passes the checks."""
    path = pathlib.Path(f)
    text = path.read_text(encoding="utf-8", errors="replace")
    rel = os.path.relpath(f, work)
    body, partial = numbered(text, [l for l, _m in errs_of_f])
    prompt = PROMPT.format(target=target, rel=rel, n=len(errs_of_f),
                           errors="\n".join(f"{rel}:{l}: {m}" for l, m in sorted(errs_of_f)[:80]),
                           entries=entry_text, api=api_facts(idx, text, [m for _l, m in errs_of_f]),
                           text=body, partial=partial)
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD"}
    # thinking is ~90% of a single-shot answer's tokens (measured: 10k of 10.8k on one 12-error file) and
    # the first tier fixes most files without it; the next tier gets a budget
    env["MAX_THINKING_TOKENS"] = str(thinking)
    t0 = time.time()
    r = subprocess.run(["claude", "-p", "--model", model_id, "--system-prompt", SYSTEM, "--tools", "",
                        "--output-format", "json"], input=prompt, cwd=work, env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout)
    try:
        d = json.loads(r.stdout)
    except ValueError:
        d = {"result": (r.stdout + r.stderr)[-400:], "total_cost_usd": 0, "is_error": True}
    ans = d.get("result") or ""
    new, why = apply_blocks(text, parse_blocks(ans))
    if new is not None:
        why = guard(text, new)
        if why is None and new != text:
            path.write_text(new, encoding="utf-8")
    rule = re.search(r'^RULE:\s*(.+)$', ans, re.M)
    rule = rule.group(1).strip().strip("`").replace("<TAB>", "\t") if rule and not rule.group(1).strip().lower().startswith("none") else None
    return {"mode": "single", "files": [rel], "errors_in": len(errs_of_f), "usd": d.get("total_cost_usd") or 0,
            "usage": d.get("usage"), "turns": d.get("num_turns"), "secs": round(time.time() - t0),
            "applied": why is None and new is not None, "rejected": why, "rule": rule, "result": ans[-600:],
            "prompt_chars": len(prompt)}


def self_check():
    import tempfile
    ok = True
    t = "class A {\n  void f() {\n    old(1);\n  }\n  void g() { x(); }\n}\n"
    new, why = apply_blocks(t, parse_blocks("<<<<<<< SEARCH\n    old(1);\n=======\n    renamed(1);\n>>>>>>> REPLACE\nNEEDS: none"))
    ok &= why is None and "renamed(1);" in new
    _n, why = apply_blocks(t, parse_blocks("<<<<<<< SEARCH\n  void\n=======\n  int\n>>>>>>> REPLACE"))   # ambiguous
    ok &= why is not None and "matched 2" in why
    _n, why = apply_blocks(t, parse_blocks("<<<<<<< SEARCH\nnot there\n=======\nx\n>>>>>>> REPLACE"))
    ok &= why is not None
    # the model re-indented by 4 (as if the line-number column were code): matched loosely, re-indented back
    new, why = apply_blocks(t, parse_blocks("<<<<<<< SEARCH\n      void f() {\n        old(1);\n=======\n"
                                            "      void f() {\n        renamed(1);\n        more();\n>>>>>>> REPLACE"))
    ok &= why is None and "  void f() {\n    renamed(1);\n    more();\n  }" in new
    big = "class B {\n" + "".join(f"  int m{i}() {{ return {i}; }}\n" for i in range(60)) + "}\n"
    ok &= guard(big, "class B {\n}\n") is not None                                              # mass deletion
    ok &= guard(t, t.replace("    old(1);", "    // old(1);\n    // more();\n    // x(2);")) is not None  # commented out
    ok &= guard(t, t.replace("old(1);", "throw new UnsupportedOperationException();")) is not None
    ok &= guard(t, t.replace("old(1);", "renamed(1);")) is None
    with tempfile.TemporaryDirectory() as d:
        g = pathlib.Path(d, "net/x"); g.mkdir(parents=True)
        (g / "Entity.java").write_text("package net.x;\npublic abstract class Entity {\n"
                                       "    public boolean igniteForSeconds(float s) { return true; }\n"
                                       "    public int getId() { return 0; }\n}\n", encoding="utf-8")
        idx = SourceIndex(d)
        facts = api_facts(idx, "import net.x.Entity;\n", ["cannot find symbol: method setSecondsOnFire(int) [location: Entity]",
                                                          "cannot find symbol: class LivingHurtEvent"])
        ok &= "net.x.Entity" in facts and "LivingHurtEvent: NOT in the target's sources" in facts
        body, part = numbered("\n".join(f"l{i}" for i in range(1000)), [500])
        ok &= part != "" and "  500| l499" in body and "(lines omitted)" in body and "  900|" not in body
    return ok
