#!/usr/bin/env python3
"""Find content the mod declares but the port no longer has: the check that catches a feature deleted to
make a compile error go away.

    python3 tools/content-census.py --work mods/<modid> [--json]

The mod's own assets/<ns>/lang/en_us.json was written by its author and carried through the port
untouched, so it is a manifest the migration cannot quietly edit (CATALOG §S5). From MC 1.21 several
kinds of content stopped being Java registrations and became datapack files; a port that deleted the
Java side to clear errors and never wrote the data side compiles, boots, passes Gate B and has silently
lost the feature. Measured: a shield mod's three enchantments (each with real behaviour) went this way,
seen only as a stub warning.

For each kind below, every `<kind>.<ns>.<id>` lang key (exactly three parts; tooltips like `.desc`
are skipped) must have its data file on a 1.21+ target, or, if the port still registers it in Java,
the id string must appear in the Java source. Exit 1 with the missing ids, 0 when complete, 2 when it
checked nothing it could (no lang file, or a pre-1.21 target) -- never a silent pass. Standard library.
"""
import argparse, json, pathlib, re, sys

# lang key prefix -> (datapack directory under data/<ns>/, catalogue entry that says how to port it)
DATA_DRIVEN = {
    "enchantment": ("enchantment", "§154/§155: one data/<ns>/enchantment/<id>.json each (description, "
                    "supported_items, weight, max_level, min_cost, max_cost, anvil_cost, slots), plus the "
                    "#minecraft:enchantment tags; code reads levels via ResourceKey<Enchantment>"),
    "painting": ("painting_variant", "§96: data/<ns>/painting_variant/<id>.json"),
    "jukebox_song": ("jukebox_song", "§35: data/<ns>/jukebox_song/<id>.json + the JUKEBOX_PLAYABLE component"),
}


def target_mc(work):
    gp = work / "gradle.properties"
    t = gp.read_text(encoding="utf-8", errors="replace") if gp.exists() else ""
    mc = (re.findall(r"(?m)^mc\s*=\s*(\S+)", t) or [None])[0]
    if mc:
        vp = work / f"versions/{mc}.properties"
        if vp.exists():
            t = vp.read_text(encoding="utf-8", errors="replace")
    return (re.findall(r"(?m)^minecraft_version\s*=\s*(\S+)", t) or [mc])[0]


def at_least_1_21(v):
    if not v:
        return False
    parts = [int(x) for x in re.findall(r"\d+", v)[:3]]
    if parts and parts[0] >= 2:          # calendar versions (26.x) are all after 1.21
        return True
    return parts[:2] >= [1, 21]


def read_lang(f):
    """The lang file as the game's lenient 1.21.1 parser saw it (a `//` line, a trailing comma), or None.
    Never skipped silently: a census that cannot read its manifest has checked nothing."""
    import importlib.util
    s = importlib.util.spec_from_file_location("fjs", pathlib.Path(__file__).resolve().parent / "fix-json-strict.py")
    fjs = importlib.util.module_from_spec(s); s.loader.exec_module(fjs)
    b = f.read_bytes()
    r = b if fjs.strict_ok(b) else fjs.repair(b)
    return json.loads(r.decode("utf-8")) if r is not None else None


def census(work):
    res = work / "src/main/resources"
    out, checked = {}, 0
    for lang in sorted(res.glob("assets/*/lang/en_us.json")):
        ns = lang.parent.parent.name
        keys = read_lang(lang)
        if keys is None:
            out.setdefault("unreadable", []).append(lang.relative_to(res).as_posix())
            continue
        java = "\n".join(f.read_text(encoding="utf-8", errors="replace")
                         for f in (work / "src/main/java").rglob("*.java")) if (work / "src/main/java").is_dir() else ""
        for kind, (folder, how) in DATA_DRIVEN.items():
            ids = sorted({k.split(".")[2] for k in keys if k.startswith(f"{kind}.{ns}.") and k.count(".") == 2})
            for i in ids:
                checked += 1
                if (res / f"data/{ns}/{folder}/{i}.json").exists():
                    continue
                if kind != "enchantment" and re.search(r'"%s"' % re.escape(i), java):
                    continue        # still registered in Java: not lost (enchantments cannot be, on 1.21+)
                out.setdefault(kind, []).append(f"{ns}:{i}")
    return out, checked


def report(missing):
    lines = []
    if "unreadable" in missing:
        lines.append("the mod's lang file is not JSON even read leniently, so the census cannot check it and "
                     "26.x will skip it (§S8): " + ", ".join(missing["unreadable"]))
    for kind, ids in missing.items():
        if kind == "unreadable":
            continue
        lines.append(f"{len(ids)} {kind}(s) the mod declares (its lang file names them) have no "
                     f"data/<ns>/{DATA_DRIVEN[kind][0]}/ file, so the port no longer has them: {', '.join(ids)}")
        lines.append(f"  port them (CATALOG {DATA_DRIVEN[kind][1]}); the original classes are in decompiled-raw/ "
                     "and their behaviour (event handlers, level checks) must keep working.")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work"); ap.add_argument("--json", action="store_true"); ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    work = pathlib.Path(a.work).resolve()
    mc = target_mc(work)
    if not at_least_1_21(mc):
        print(f"content-census: target {mc or '?'} is before 1.21; nothing here is data-driven yet (checked nothing)")
        return 2
    missing, checked = census(work)
    if a.json:
        print(json.dumps({"target": mc, "checked": checked, "missing": missing}))
    elif missing:
        print(report(missing))
    else:
        print(f"content-census: {checked} declared data-driven entr(ies), all present" if checked
              else "content-census: no data-driven content declared (checked nothing)")
    return 1 if missing else (0 if checked else 2)


def self_check():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        w = pathlib.Path(d)
        (w / "src/main/resources/assets/mymod/lang").mkdir(parents=True)
        (w / "src/main/resources/assets/mymod/lang/en_us.json").write_text(json.dumps({
            "enchantment.mymod.spikes": "Spikes", "enchantment.mymod.spikes.desc": "x",
            "enchantment.mymod.payback": "Payback", "item.mymod.x": "X"}), encoding="utf-8")
        (w / "gradle.properties").write_text("minecraft_version=1.21.1\n", encoding="utf-8")
        (w / "src/main/java").mkdir(parents=True)
        m, n = census(w)
        ok = m == {"enchantment": ["mymod:payback", "mymod:spikes"]} and n == 2
        (w / "src/main/resources/data/mymod/enchantment").mkdir(parents=True)
        for i in ("spikes", "payback"):
            (w / f"src/main/resources/data/mymod/enchantment/{i}.json").write_text("{}", encoding="utf-8")
        ok &= census(w)[0] == {}
        (w / "src/main/resources/assets/mymod/lang/en_us.json").write_text(
            '{\n "enchantment.mymod.spikes": "S",\n// "x": "y",\n "enchantment.mymod.payback": "P",\n "enchantment.mymod.gone": "G",\n}',
            encoding="utf-8")
        ok &= census(w)[0] == {"enchantment": ["mymod:gone"]}      # a lenient-only file is read, not skipped
        ok &= at_least_1_21("26.2") and at_least_1_21("1.21.1") and not at_least_1_21("1.20.1")
        (w / "gradle.properties").write_text("mc=26.2\n", encoding="utf-8")
        (w / "versions").mkdir(); (w / "versions/26.2.properties").write_text("minecraft_version=26.2\n", encoding="utf-8")
        ok &= target_mc(w) == "26.2"
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
