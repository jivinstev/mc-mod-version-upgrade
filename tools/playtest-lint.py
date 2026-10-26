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
    @aim entity <type id | selector> [if-present] | @aim pos <x> <y> <z> (~ relative) | @aim look <yaw> <pitch>
                                   (if-present: no such entity passes -- a repeated strike after the kill)
    @key <mapping name> [hold <duration>]   a key mapping by name (key.attack, key.hotbar.1, a mod's own), or a raw
                                   key by its name (key.keyboard.escape, key.mouse.left); default hold 2t
    @click slot <N> | @click button "<text>"
    @expect <kind> [<match>] [gone] [within <duration>]   kinds: sound subtitle chat title actionbar bossbar screen error;
                                   match = case-insensitive substring of the text or of a translation key, or /regex/
    @shot <name>                   a screenshot <out>/<NN>-<name>.png
    @goto structure <id | #tag> [in <dimension>]   travel to the nearest naturally generated one (/locate), inside its start piece
    @repeat <N> [until <command>] ... @end-repeat   the block's lines up to N times, stopping when the command succeeds
    @repeat each <selector> max <N> [until <command>] ... @end-repeat
                                   the block for every entity the selector matched when it began, up to N times each
                                   (N is required: a condition never met FAILs after N runs, naming N);
                                   {it} in its lines (commands, @aim entity, until) is that entity's UUID
    @click at <x>% <y>%            a click at that point of the open screen, for options drawn without widgets
    @allow error <regex>           ERROR log lines matching it do not fail a step (header: the whole run; in a step: that step)
    @world [flat|normal] [structures] [seed=<n>]   header only: the fresh world (default flat, seed 0;
                                   structures: the flat world's villages and strongholds, off by default)
    @continue-on-fail              header only: run the next step after a failed one
    @end                           stop reading here
Standard library only.
"""
import pathlib, re, sys

KINDS = ("sound", "subtitle", "chat", "title", "actionbar", "bossbar", "screen", "error")
DIRECTIVES = ("@intent", "@step", "@assert", "@wait", "@aim", "@key", "@click", "@expect", "@shot", "@goto", "@repeat",
              "@end-repeat", "@allow",
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
    block = None                    # the line of the @repeat that is open, if any
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
            if block:
                probs.append((block, "@repeat without @end-repeat (the script ends inside it)"))
            break
        if block and d in ("@repeat", "@step", "@world", "@continue-on-fail", "@intent", "@allow"):
            probs.append((no, f"{d} cannot be inside an @repeat block (close it with @end-repeat first)")); continue
        if d == "@end-repeat":
            if not block:
                probs.append((no, "@end-repeat without @repeat"))
            block = None
            continue
        if d == "@repeat":
            if not in_step:
                steps.append("main"); in_step = True
            m = re.fullmatch(r"(?:(\d+)|each\s+(\S+)\s+max\s+(\d+))(?:\s+until\s+(.+))?", arg)
            if not m:
                probs.append((no, "@repeat <N> [until <command>] | @repeat each <selector> max <N> [until <command>]"
                                  " (every block needs its bound N)"))
            else:
                n_ = int(m.group(1) or m.group(3) or 1)
                if not 1 <= n_ <= 1000:
                    probs.append((no, "@repeat: N is 1-1000"))
                elif m.group(1) and not m.group(4) and n_ < 2:
                    probs.append((no, f"@repeat {n_} does nothing a block would not"))
            block = no
            continue
        if d in ("@world", "@continue-on-fail"):
            if in_step:
                probs.append((no, f"{d} belongs in the header, before the first step"))
            if d == "@world":
                for w in arg.split():
                    if w not in ("flat", "normal", "default", "structures") and not re.fullmatch(r"seed=-?\d+", w):
                        probs.append((no, f"@world takes flat|normal, structures and seed=<n>, not {w!r}"))
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
            pct = re.fullmatch(r"(\d+(?:\.\d+)?)%\s+(\d+(?:\.\d+)?)%", rest) if k == "at" else None
            if not ((k == "slot" and re.fullmatch(r"\d+", rest)) or (k == "button" and rest)
                    or (pct and float(pct.group(1)) <= 100 and float(pct.group(2)) <= 100)):
                probs.append((no, '@click slot <N> | @click button "<text>" | @click at <x>% <y>%'))
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
        elif d == "@goto":
            if not re.fullmatch(r"structure\s+#?[a-z0-9_.-]+:[a-z0-9_./-]+(\s+in\s+[a-z0-9_.-]+:[a-z0-9_./-]+)?", arg):
                probs.append((no, "@goto structure <id or #tag> [in <dimension>]"))
    if block:
        probs.append((block, "@repeat without @end-repeat"))
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
        "# header", "@intent prove it", "@world flat structures seed=3", "@continue-on-fail", "@allow error Narrator",
        "@step one", "/say hi", "@expect chat hi within 5s", "@wait 10t", "@wait 1.5s",
        "@aim entity minecraft:zombie", "@aim entity @e[type=zombie,limit=1]", "@aim entity minecraft:zombie if-present", "@goto structure #minecraft:village", "@goto structure minecraft:fortress in minecraft:the_nether",
        "@repeat each @e[tag=t] max 5 until execute unless entity {it}", "@aim entity {it}", "@end-repeat", "@click at 50% 40%", "@aim pos ~ ~1 10", "@aim look 90 -10",
        "@key key.attack", "@key key.forward hold 20t", "@click slot 36", '@click button "Done"',
        "@expect sound entity.zombie.* gone within 2s", "@expect subtitle /zombie (hurts|groans)/", "@expect error gone",
        "@expect screen none", "@shot after-fight", "@assert execute if entity @s", "@step two", "@allow error Foo.*", "@end",
        "@not-a-directive after end is ignored"])
    p, s = lint(good)
    expect(p == [] and s["steps"] == ["one", "two"] and s["lines"] == 23, f"a script using every directive is clean: {p} {s}")
    for src, want in [("@step a\n@repeat each @e[tag=t] until execute unless entity {it}\n@wait 1t\n@end-repeat", "bound N"),
                      ("@step a\n@repeat until execute if entity @s\n@wait 1t\n@end-repeat", "bound N"),
                      ("@step a\n@repeat 3\n@wait 1t", "without @end-repeat"), ("@step a\n@end-repeat", "without @repeat"),
                      ("@step a\n@repeat 3\n@step b\n@end-repeat", "cannot be inside"),
                      ("@step a\n@click at 150% 2%", "@click at")]:
        expect(any(want in m for _n, m in lint(src)[0]), f"{src!r} is refused naming {want!r}: {lint(src)[0]}")
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
