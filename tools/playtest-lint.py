#!/usr/bin/env python3
"""Check a .playtest script without Minecraft: the grammar the Gate C carrier's playtest mode runs
(templates/smoke-harness/.../PlaytestRunner.java, -Dsmokeharness.testmode=playtest -Dsmokeharness.playtest=<file>).

    python3 tools/playtest-lint.py FILE [FILE ...]    one line per problem (FILE:LINE: message); exit 1 on any
    python3 tools/playtest-lint.py --self-check

The grammar (one line at a time; blank lines and '# comments' are ignored):
    <command>                      a Minecraft command, run as the player (a leading '/' is accepted); fails the step on failure
    @assert <command>              the command must succeed (e.g. execute if entity @e[type=...])
    @intent <text>                 the script's intent (in the header) or the step's (inside a step)
    @step <name>                   starts a step; lines before the first @step form step "main"
    @wait <N>t | <N>s              ticks or seconds (a second is 20 ticks)
    @aim entity <type id | selector> | @aim pos <x> <y> <z> (~ relative) | @aim look <yaw> <pitch>
    @key <mapping name> [hold <duration>]   a key mapping by name (key.attack, key.hotbar.1, a mod's own), or a raw
                                   key by its name (key.keyboard.escape, key.mouse.left); default hold 2t
    @click slot <N> | @click button "<text>"
    @expect <kind> [<match>] [gone] [within <duration>]   kinds: sound subtitle chat title actionbar bossbar screen error;
                                   match = case-insensitive substring of the text or of a translation key, or /regex/
    @shot <name>                   a screenshot <out>/<NN>-<name>.png
    @allow error <regex>           ERROR log lines matching it do not fail a step (header: the whole run; in a step: that step)
    @world [flat|normal] [seed=<n>]   header only: the fresh world (default flat, seed 0)
    @continue-on-fail              header only: run the next step after a failed one
    @end                           stop reading here
Standard library only.
"""
import pathlib, re, sys

KINDS = ("sound", "subtitle", "chat", "title", "actionbar", "bossbar", "screen", "error")
DIRECTIVES = ("@intent", "@step", "@assert", "@wait", "@aim", "@key", "@click", "@expect", "@shot", "@allow",
              "@world", "@continue-on-fail", "@end")
DURATION = re.compile(r"\d+(?:\.\d+)?[ts]")
NUM = re.compile(r"-?\d+(?:\.\d+)?")


def _coord(s):
    return bool(NUM.fullmatch(s[1:]) if s.startswith("~") and len(s) > 1 else s == "~" or NUM.fullmatch(s))


def _regex(s):
    try:
        re.compile(s)
        return None
    except re.error as e:
        return str(e)


def lint(text):
    """-> (problems [(line, message)], summary {steps, lines, header}) for a script's text."""
    probs, steps, in_step, n = [], [], False, 0
    lines = text.splitlines()
    if lines and lines[0].startswith("﻿"):
        lines[0] = lines[0][1:]
    for no, raw in enumerate(lines, 1):
        t = raw.strip()
        if not t or t.startswith("#"):
            continue
        if not t.startswith("@"):
            if not in_step:
                steps.append("main"); in_step = True
            if t in ("/", ""):
                probs.append((no, "an empty command"))
            n += 1
            continue
        d, _, arg = t.partition(" ")
        d, arg = d.lower(), arg.strip()
        if d not in DIRECTIVES:
            probs.append((no, f"unknown directive {d} (known: {' '.join(DIRECTIVES)})")); continue
        if d == "@end":
            break
        if d in ("@world", "@continue-on-fail"):
            if in_step:
                probs.append((no, f"{d} belongs in the header, before the first step"))
            if d == "@world":
                for w in arg.split():
                    if w not in ("flat", "normal", "default") and not re.fullmatch(r"seed=-?\d+", w):
                        probs.append((no, f"@world takes flat|normal and seed=<n>, not {w!r}"))
            continue
        if d == "@intent":
            continue
        if d == "@step":
            if not arg:
                probs.append((no, "@step needs a name"))
            steps.append(arg); in_step = True; continue
        if d == "@allow":
            k, _, rx = arg.partition(" ")
            if k != "error" or not rx.strip():
                probs.append((no, "@allow error <regex>"))
            elif _regex(rx.strip()):
                probs.append((no, f"@allow error: bad regex: {_regex(rx.strip())}"))
            continue
        if not in_step:
            steps.append("main"); in_step = True
        n += 1
        a = arg.split()
        if d == "@assert":
            if not arg:
                probs.append((no, "@assert needs a command"))
        elif d == "@wait":
            if not DURATION.fullmatch(arg):
                probs.append((no, f"a duration is <N>t (ticks) or <N>s (seconds), not {arg!r}"))
        elif d == "@aim":
            how = a[0] if a else ""
            if how == "entity":
                if len(a) < 2:
                    probs.append((no, "@aim entity <type id or selector>"))
            elif how == "pos":
                if len(a) != 4 or not all(_coord(x) for x in a[1:]):
                    probs.append((no, "@aim pos <x> <y> <z> (numbers, ~ relative)"))
            elif how == "look":
                if len(a) != 3 or not all(NUM.fullmatch(x) for x in a[1:]):
                    probs.append((no, "@aim look <yaw> <pitch>"))
            else:
                probs.append((no, f"@aim entity|pos|look, not {how!r}"))
        elif d == "@key":
            if not (len(a) == 1 or (len(a) == 3 and a[1] == "hold" and DURATION.fullmatch(a[2]))):
                probs.append((no, "@key <key.mapping.name> [hold <duration>]"))
        elif d == "@click":
            k, _, rest = arg.partition(" ")
            rest = rest.strip()
            if not ((k == "slot" and re.fullmatch(r"\d+", rest)) or (k == "button" and rest)):
                probs.append((no, '@click slot <N> | @click button "<text>"'))
        elif d == "@expect":
            if not a or a[0].lower() not in KINDS:
                probs.append((no, f"unknown @expect kind {a[0] if a else ''!r} (kinds: {' '.join(sorted(KINDS))})")); continue
            kind, rest = a[0].lower(), a[1:]
            if len(rest) >= 2 and rest[-2] == "within":
                if not DURATION.fullmatch(rest[-1]):
                    probs.append((no, f"within takes a duration (<N>t or <N>s), not {rest[-1]!r}"))
                rest = rest[:-2]
            if rest and rest[-1] == "gone":
                rest = rest[:-1]
            match = " ".join(rest)
            if len(match) >= 2 and match[0] == match[-1] == '"':
                match = match[1:-1]
            if not match and kind != "error":
                probs.append((no, f"@expect {kind} needs a match"))
            if len(match) >= 2 and match.startswith("/") and match.endswith("/") and _regex(match[1:-1]):
                probs.append((no, f"@expect: bad /regex/: {_regex(match[1:-1])}"))
        elif d == "@shot":
            if not re.fullmatch(r"[A-Za-z0-9._-]+", arg):
                probs.append((no, "@shot <name> (letters, digits, . _ -)"))
    if not steps:
        probs.append((0, "the script has no steps"))
    return probs, {"steps": steps, "lines": n}


def self_check():
    ok = True

    def expect(cond, what):
        nonlocal ok
        if not cond:
            print(f"self-check: FAIL {what}")
            ok = False

    good = "\n".join([
        "# header", "@intent prove it", "@world flat seed=3", "@continue-on-fail", "@allow error Narrator",
        "@step one", "/say hi", "@expect chat hi within 5s", "@wait 10t", "@wait 1.5s",
        "@aim entity minecraft:zombie", "@aim entity @e[type=zombie,limit=1]", "@aim pos ~ ~1 10", "@aim look 90 -10",
        "@key key.attack", "@key key.forward hold 20t", "@click slot 36", '@click button "Done"',
        "@expect sound entity.zombie.* gone within 2s", "@expect subtitle /zombie (hurts|groans)/", "@expect error gone",
        "@expect screen none", "@shot after-fight", "@assert execute if entity @s", "@step two", "@allow error Foo.*", "@end",
        "@not-a-directive after end is ignored"])
    p, s = lint(good)
    expect(p == [] and s["steps"] == ["one", "two"] and s["lines"] == 18, f"a script using every directive is clean: {p} {s}")
    for bad, want in (("@step a\n@wait 10", "a duration"), ("@step a\n@expect smell roses", "unknown @expect kind"),
                      ("@step a\n@aim at zombie", "@aim entity|pos|look"), ("@step a\n@key key.use hold", "@key <key"),
                      ("@step a\n@click 3", "@click slot"), ("@step a\n@world flat", "header"),
                      ("@step a\n@expect chat", "needs a match"), ("@step a\n@expect chat /(/", "bad /regex/"),
                      ("@step a\n@shot bad name", "@shot <name>"), ("@step a\n@frobnicate", "unknown directive"),
                      ("@world tilted", "@world takes"), ("# nothing", "no steps"), ("@step a\n@allow warn x", "@allow error"),
                      ("@step a\n@expect title x within soon", "within takes a duration")):
        p, _ = lint(bad)
        expect(any(want in m for _, m in p), f"{bad!r} -> a problem containing {want!r}: {p}")
    p, s = lint("say hi\n@expect chat hi")
    expect(p == [] and s["steps"] == ["main"], "lines before any @step form step 'main'")
    p, _ = lint("@step a\n@wait 5x")
    expect(p and p[0][0] == 2, "a problem names its line")
    fixtures = sorted((pathlib.Path(__file__).resolve().parent.parent / "templates/gatec-carrier/playtests").glob("*.playtest"))
    expect(len(fixtures) >= 2, f"the carrier's fixtures are present: {fixtures}")
    for f in fixtures:
        p, _ = lint(f.read_text(encoding="utf-8"))
        expect(p == [], f"{f.name} lints clean: {p}")
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main(argv):
    if "--self-check" in argv:
        return self_check()
    files = [a for a in argv if not a.startswith("-")]
    if not files:
        print(__doc__.strip().splitlines()[0]); print("usage: playtest-lint.py FILE [FILE ...] | --self-check")
        return 2
    bad = 0
    for f in files:
        p = pathlib.Path(f)
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as e:
            print(f"{f}: cannot read: {e}"); bad += 1; continue
        probs, s = lint(text)
        for no, m in probs:
            print(f"{p.as_posix()}:{no}: {m}")
        bad += bool(probs)
        if not probs:
            print(f"{p.as_posix()}: OK ({len(s['steps'])} step(s), {s['lines']} line(s))")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
