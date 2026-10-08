#!/usr/bin/env python3
"""Measure what a port must change for each dependency's API -- per dependency, before any model runs.

    python3 tools/dep-api.py --repo <mod> --mc <target>                         # resolve target jars via port-deps
    python3 tools/dep-api.py --repo <mod> --jar <target.jar> [--jar ...] [--src-tree <ported dep's src/main/java>]
    python3 tools/dep-api.py --self-check

A mod "integrates with a dozen mods" can mean a dozen API rewrites or none. This answers which, from the
jars, the way tools/dep-moves.py answers class moves. For every dependency whose target build (or ported
source tree) is known, it reports what the mod USES and whether that still exists in the target version:

  imports      every class the mod imports from it: present, or missing (dep-moves finds the moved ones)
  overrides    every method the mod overrides on one of ITS types (`@Override` in a class that extends or
               implements a dependency class): same signature, signature changed (the new signatures are
               printed, so a worker is handed them), or gone from the type
  ids          `"<modid>:<path>"` strings for a mod the source only reaches by id (`isLoaded("x")` guards,
               registry lookups): the path is found in the target jar or not

and one verdict per dependency -- unchanged / renames only / signature changes / gone / no target build -- with
how it is reached: imports, ids only, or not at all (a dev-runtime jar the code never names). Signature
comparison is by simple type names and arity, generic arguments dropped: it says where to look, it does not
rewrite. Standard library plus `javap` from the JDK.
"""
import argparse, collections, functools, importlib.util, json, pathlib, re, subprocess, sys, zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
IMPORT = re.compile(r"^import\s+(?:static\s+)?([\w.]+?)(?:\.\*)?\s*;", re.M)
CLASS = re.compile(r"\b(?:class|interface|enum|record)\s+(\w+)(?:<[^{]*?>)?(?:\([^)]*\))?\s*"
                   r"(?:extends\s+([\w.<>?,\s]+?))?\s*(?:implements\s+([\w.<>?,\s]+?))?\s*\{", re.S)
OVERRIDE = re.compile(r"@Override\s+(?:@\w+(?:\([^)]*\))?\s+)*(?:public|protected)?\s*(?:static\s+|final\s+|"
                      r"synchronized\s+|default\s+)*(?:<[^>]+>\s+)?[\w.<>\[\]?,\s]+?\s+(\w+)\s*\(([^)]*)\)")
ID = re.compile(r'"([a-z0-9_.-]+):([a-z0-9_./-]+)"'
                r'|(?:ResourceLocation|fromNamespaceAndPath)\(\s*"([a-z0-9_.-]+)"\s*,\s*"([a-z0-9_./-]+)"\s*\)')
LOADED = re.compile(r'isLoaded\(\s*"([a-z0-9_.-]+)"\s*\)')


def simple(t):
    t = re.sub(r"<.*>", "", t).replace("...", "[]").strip()
    t = re.sub(r"@\w+(\([^)]*\))?\s*", "", t).replace("final ", "").strip()
    return re.split(r"[.$]", t.split()[0] if t else "")[-1]


def params(s):
    out, depth, cur = [], 0, ""
    for ch in s:
        depth += ch == "<"; depth -= ch == ">"
        if ch == "," and depth == 0:
            out.append(cur); cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return [simple(" ".join(p.split()[:-1]) if len(p.split()) > 1 else p) for p in out]


TYPEVAR = re.compile(r"^[A-Z][A-Z0-9]?$")


def sig_matches(mine, theirs):
    """Same arity, and each parameter equal -- a type variable (T, E, K...) in the dependency's signature is
    filled by the mod's concrete type (`class M extends GeoModel<Wraith>` overrides getModelResource(T))."""
    return len(mine) == len(theirs) and all(a == b or TYPEVAR.match(b) for a, b in zip(mine, theirs))


def jar_modids(path=None, tree=None):
    try:
        if path:
            z = zipfile.ZipFile(path); names = z.namelist()
            t = "".join(z.read(n).decode("utf-8", "replace") for n in names
                        if n in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"))
        else:
            res = pathlib.Path(tree).parent / "resources/META-INF"
            t = "".join(f.read_text(encoding="utf-8", errors="replace") for f in res.glob("*mods.toml"))
        ids = re.findall(r'^\s*modId\s*=\s*"([^"]+)"', t, re.M)
        return [i for i in ids if i not in ("minecraft", "neoforge", "forge")]
    except Exception:
        return []


class Jar:
    """The classes of one target jar (or a ported source tree), and javap of any of them on demand."""
    def __init__(self, path=None, tree=None):
        self.path, self.tree = path, tree
        if path:
            self.classes = {n[:-6].replace("/", ".") for n in zipfile.ZipFile(path).namelist() if n.endswith(".class")}
            self.blob = None
        else:
            self.classes = {str(f.relative_to(tree))[:-5].replace("/", ".") for f in pathlib.Path(tree).rglob("*.java")}
        self.roots = {".".join(c.split(".")[:2]) for c in self.classes if c.count(".") >= 2}
        self.modids = jar_modids(path, tree)
        self.name = (self.modids or [pathlib.Path(path or tree).name])[0]

    def owns(self, fq):
        return any(fq == r or fq.startswith(r + ".") for r in self.roots)

    def resolve(self, fq):
        """Outer.Inner and static-member imports resolve to the class that holds them."""
        parts = fq.split(".")
        for i in range(len(parts), 0, -1):
            c = ".".join(parts[:i]) + "".join("$" + x for x in parts[i:])
            if c in self.classes:
                return c
            if i < len(parts) and ".".join(parts[:i]) in self.classes:
                return ".".join(parts[:i])
        return None

    @functools.lru_cache(maxsize=None)
    def members(self, cls):
        """{name: [(simple param types)]} over cls and its supertypes inside this jar."""
        out = collections.defaultdict(list)
        if self.tree:                                  # a ported source tree: read the declarations
            f = pathlib.Path(self.tree) / (cls.split("$")[0].replace(".", "/") + ".java")
            if f.is_file():
                for m in re.finditer(r"(?:public|protected|default)\s+[\w.<>\[\]?,\s]+?\s+(\w+)\s*\(([^)]*)\)",
                                     f.read_text(encoding="utf-8", errors="replace")):
                    out[m.group(1)].append(tuple(params(m.group(2))))
            return out
        r = subprocess.run(["javap", "-p", "-cp", str(self.path), cls], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        supers = []
        for line in r.stdout.splitlines():
            h = re.match(r".*?\b(?:class|interface)\s+[\w.$<>?,\s]+?(?:\s+extends\s+([\w.$<>?,\s]+?))?"
                         r"(?:\s+implements\s+([\w.$<>?,\s]+?))?\s*\{$", line)
            if h:
                for grp in h.groups():
                    supers += [re.sub(r"<.*", "", s).strip() for s in re.split(r",(?![^<]*>)", grp or "") if s.strip()]
                continue
            m = re.match(r"\s+.*?\s([\w$]+)\((.*)\)(?:\s+throws [\w.$, ]+)?;$", line)
            if m:
                out[m.group(1)].append(tuple(simple(p) for p in m.group(2).split(",") if p.strip()))
        for s in supers:
            if s in self.classes:
                for k, v in self.members(s).items():
                    out[k] += v
        return out


def source_files(repo):
    try:
        sp = importlib.util.spec_from_file_location("srcsets", ROOT / "tools/srcsets.py")
        m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)
        return [f for d in m.java_dirs(repo) for f in pathlib.Path(d).rglob("*.java")]
    except Exception:
        return list((pathlib.Path(repo) / "src").rglob("*.java"))


def analyse(files, jars, mod_ids=()):
    """jars: {name: Jar}. Returns {name: report} plus an 'ids' report for mods reached only by id."""
    rep = {n: {"imports": set(), "missing": set(), "files": set(), "ok": [], "changed": [], "gone": []}
           for n in jars}
    loaded, ids = set(), collections.defaultdict(set)
    for f in files:
        t = f.read_text(encoding="utf-8", errors="replace")
        loaded |= set(LOADED.findall(t))
        for m in ID.finditer(t):
            ns, path = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
            ids[ns].add((path, f.name))
        imps = IMPORT.findall(t)
        simple_to_fq = {i.split(".")[-1]: i for i in imps}
        for i in imps:
            for n, j in jars.items():
                if j.owns(i):
                    r = rep[n]; r["imports"].add(i); r["files"].add(f.name)
                    if not j.resolve(i):
                        r["missing"].add(i)
        for cm in CLASS.finditer(t):
            supers = [re.sub(r"<.*", "", s).strip() for s in re.split(r",(?![^<]*>)", " ".join(
                x for x in (cm.group(2), cm.group(3)) if x)) if s.strip()]
            deps = []
            for s in supers:
                fq = simple_to_fq.get(s.split(".")[0], s)
                fq = fq + s[len(s.split(".")[0]):] if "." in s and fq != s else fq
                for n, j in jars.items():
                    c = j.resolve(fq)
                    if c:
                        deps.append((n, j, c))
            if not deps:
                continue
            for om in OVERRIDE.finditer(t, cm.end()):
                name, ps = om.group(1), tuple(params(om.group(2)))
                for n, j, c in deps:
                    sigs = j.members(c).get(name)
                    if sigs is None:
                        continue                                  # not this dependency's method (vanilla / other)
                    where = f"{f.name}: {cm.group(1)}.{name}({', '.join(ps)})"
                    if any(sig_matches(ps, sig) for sig in sigs):
                        rep[n]["ok"].append(where)
                    else:
                        rep[n]["changed"].append(where + "  ->  now " + " | ".join(
                            f"{name}({', '.join(s)})" for s in sorted(set(sigs))))
                    break
    for n, r in rep.items():
        parts = [f"{len(r['missing'])} imported class(es) missing (run dep-moves for the moved ones)"] if r["missing"] else []
        parts += [f"{len(r['changed'])} overridden method(s) changed signature"] if r["changed"] else []
        r["verdict"] = ("not referenced (dev runtime only)" if not r["imports"] else "; ".join(parts) or "unchanged")
    by_id = {}
    imported = {m for n, j in jars.items() if rep[n]["imports"] for m in j.modids}
    dep_ids = {m for j in jars.values() for m in j.modids}
    for ns in sorted(set(mod_ids) | loaded | (set(ids) & dep_ids)):
        if ns in ("minecraft", "forge", "neoforge", "c") or ns in imported:
            continue
        by_id[ns] = sorted(ids.get(ns, ()))
    return rep, by_id, loaded


def id_found(jar_path, path):
    """Is an id's path anywhere in the jar: entry names (assets/data) or class constants?"""
    z = zipfile.ZipFile(jar_path)
    leaf = path.split("/")[-1].encode()
    return any(leaf.decode() in n for n in z.namelist()) or any(
        leaf in z.read(n) for n in z.namelist() if n.endswith((".class", ".json")))


def render(rep, by_id, loaded, id_jars, jars=None):
    """id_jars: {modid: jar path}; a jar passed in `jars` also serves its own mod ids, so a mod the code reaches
    only by id is checked against the jar already fetched for it, and reported once."""
    id_jars = dict(id_jars)
    for j in (jars or {}).values():
        for m in j.modids:
            if j.path:
                id_jars.setdefault(m, j.path)
    L = []
    for n, r in rep.items():
        if not r["imports"] and any(m in by_id for m in getattr((jars or {}).get(n), "modids", [])):
            continue                                              # reported below, as reached by id
        reach = "imports" if r["imports"] else "-"
        L.append(f"{n}: {r['verdict']}  [imports {len(r['imports'])} in {len(r['files'])} file(s); overrides "
                 f"{len(r['ok'])} same, {len(r['changed'])} changed]")
        L += [f"    missing  {m}" for m in sorted(r["missing"])]
        L += [f"    changed  {c}" for c in r["changed"]]
    for ns, uses in by_id.items():
        guard = " (isLoaded guard)" if ns in loaded else " (no isLoaded guard)"
        if not uses:
            L.append(f"{ns}: reached by id only{guard}, no ids used")
            continue
        jp = id_jars.get(ns)
        if jp:
            miss = [p for p, _ in uses if not id_found(jp, p)]
            L.append(f"{ns}: reached by id only{guard}; {len(uses)} id(s), {len(miss)} not found in the target jar")
            L += [f"    not found  {ns}:{p}" for p in miss]
        else:
            L.append(f"{ns}: reached by id only{guard}; {len(uses)} id(s), no target jar given -- unchecked")
    return "\n".join(L)


def self_check():
    import tempfile
    ok = True
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        lib = d / "lib/com/example/lib"; lib.mkdir(parents=True)
        (lib / "Base.java").write_text("package com.example.lib; public interface Base {"
                                       " default void tick(String a, int b) {}"
                                       " default void sound(Object ctx) {} }", encoding="utf-8")
        (lib / "Helper.java").write_text("package com.example.lib; public class Helper {}", encoding="utf-8")
        (lib / "Model.java").write_text("package com.example.lib; public abstract class Model<T> {"
                                        " public abstract String res(T t); }", encoding="utf-8")
        out = d / "classes"; out.mkdir()
        subprocess.run(["javac", "-d", str(out), str(lib / "Base.java"), str(lib / "Helper.java"), str(lib / "Model.java")], check=True,
                       capture_output=True)
        jar = d / "lib.jar"
        with zipfile.ZipFile(jar, "w") as z:
            for c in out.rglob("*.class"):
                z.write(c, str(c.relative_to(out)))
            z.writestr("assets/lib/lang/en_us.json", '{"attribute.lib.max_mana": "x"}')
        src = d / "src/my"; src.mkdir(parents=True)
        (src / "Ring.java").write_text("""package my;
import com.example.lib.Base;
import com.example.lib.Gone;
public class Ring implements Base {
    @Override
    public void tick(String a, int b) {}
    @Override
    public void sound() {}
    @Override
    public String toString() { return ""; }
    void x() { if (net.ModList.get().isLoaded("spells")) get("spells:max_mana"); get(new ResourceLocation("spells", "ghost_thing")); }
}""", encoding="utf-8")
        (src / "M.java").write_text("package my;\nimport com.example.lib.Model;\npublic class M extends Model<Ring> {\n"
                                    "    @Override\n    public String res(Ring r) { return \"\"; }\n}", encoding="utf-8")
        rep, by_id, loaded = analyse(list(src.rglob("*.java")), {"lib": Jar(path=jar)})
        r = rep["lib"]
        ok &= r["missing"] == {"com.example.lib.Gone"} and len(r["ok"]) == 2 and len(r["changed"]) == 1
        ok &= "sound(Object)" in r["changed"][0] and "1 imported class(es) missing" in r["verdict"] \
            and "1 overridden method(s) changed" in r["verdict"]
        ok &= "toString" not in " ".join(r["ok"] + r["changed"])          # vanilla/Object overrides are not ours
        ok &= by_id.get("spells") and "spells" in loaded
        text = render(rep, by_id, loaded, {"spells": jar})
        ok &= "1 not found" in text and "spells:ghost_thing" in text and "max_mana" not in text.split("not found  ")[-1]
    ok &= params("Map<String, List<Integer>> m, final int x, String... s") == ["Map", "int", "String[]"]
    print("self-check:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main():
    if "--self-check" in sys.argv:
        return self_check()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", required=True); ap.add_argument("--mc")
    ap.add_argument("--jar", action="append", default=[], help="a dependency's TARGET jar (repeatable)")
    ap.add_argument("--src-tree", action="append", default=[], help="a ported dependency's source tree")
    ap.add_argument("--id-jar", action="append", default=[], metavar="MODID=JAR",
                    help="target jar of a mod reached only by id, to check its ids")
    ap.add_argument("--json")
    a = ap.parse_args()
    repo = pathlib.Path(a.repo).resolve()
    jars = {}
    for j in [Jar(path=p) for p in a.jar] + [Jar(tree=p) for p in a.src_tree]:
        jars[j.name] = j
    if a.mc and not jars:
        sp = importlib.util.spec_from_file_location("port_deps", ROOT / "tools/port-deps.py")
        pd = importlib.util.module_from_spec(sp); sys.modules["port_deps"] = pd; sp.loader.exec_module(pd)
        res = pd.run(repo, a.mc, out=lambda *_: None) if "out" in pd.run.__code__.co_varnames else None
        for d in (res or {}).get("deps", []):
            ch = ((d.get("res") or {}).get("versions") or {}).get("chosen")
            if d.get("kind") == "mod" and ch:
                p, msg = pd.fetch_jar(d["res"].get("fallback_provider") or d["res"]["provider"], d["res"]["id"],
                                      ch["fileId"])
                if p:
                    jars[f"{d['group']}:{d['artifact']}"] = Jar(path=p)
    if not jars:
        sys.exit("no target jars: pass --jar/--src-tree, or --mc to resolve them through port-deps")
    id_jars = dict(x.split("=", 1) for x in a.id_jar)
    rep, by_id, loaded = analyse(source_files(repo), jars)
    print(render(rep, by_id, loaded, id_jars, jars))
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps({n: {k: sorted(v) if isinstance(v, set) else v
                                                        for k, v in r.items()} for n, r in rep.items()}
                                                   | {"_ids": by_id}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
