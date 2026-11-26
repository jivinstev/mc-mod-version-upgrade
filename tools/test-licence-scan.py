#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0. If a copy of the MPL
# was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
# Copied from another project's tools/test-licence-scan.py and adapted to this repository's rules and hooks;
# THIRD_PARTY.md names the source and what was changed. This file stays MPL-2.0 inside this MIT repository (MPL-2.0 is file-level copyleft).
"""Tests for tools/licence-scan.py: every rule goes red, its near-misses stay green, both ways through hold, the
register and the bundled checks catch what they should, and the git hooks refuse a commit and a push that add an
LGPL header. Hermetic (temp repositories, jars built here), and every marker is glued together at run time so this
file is not itself a finding.

    python3 tools/test-licence-scan.py
"""
import io
import json
import os
import re
import stat
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
SCRIPT = TOOLS / "licence-scan.py"
ns = {"__name__": "licence_scan", "__file__": str(SCRIPT)}
exec(compile(SCRIPT.read_text(encoding="utf-8"), str(SCRIPT), "exec"), ns)   # run the source, never a cached module
POLICY = ns["Policy"](TOOLS / "licence-rules.json")

J = ""                                                   # the glue
LGPL = "GNU " + J + "Lesser General Public License"
GPL = "GNU " + J + "General Public License"
FSF = "Free Software " + J + "Foundation"
RESERVED = "All rights " + J + "reserved"
SPDX = "SPDX-License-" + J + "Identifier: "
ALLOW = "licence-scan: " + J + "allow"
COPR = "Copy" + J + "right (c) 2019"
HEADER = "/* " + COPR + " " + FSF + ", Inc.\n * Licensed under the " + LGPL + ", version 2.1.\n */\nint f(void);\n"


def ns_bash():
    """Git Bash on Windows (tools/gitbash.py), bash elsewhere."""
    g = {"__name__": "gitbash"}
    exec(compile((TOOLS / "gitbash.py").read_text(encoding="utf-8"), str(TOOLS / "gitbash.py"), "exec"), g)
    return g["BASH"]


def rules(line, exempt=False):
    return sorted({r for _p, _n, r, _w in ns["judge_line"](POLICY, "src/A.java", 1, line, exempt)})


class Wording(unittest.TestCase):
    def test_every_rule_goes_red(self):
        for rid, line in {
            "gnu-gpl": "// under the " + GPL,
            "gnu-lgpl": "// see the Lesser " + J + "General Public License",
            "agpl": "// the GNU Aff" + J + "ero licence",
            "fsf": "# " + COPR + " " + FSF,
            "licence-id": "// ported from libm (LG" + J + "PL-2.1)",
            "all-rights-reserved": "// " + COPR + " Somebody. " + RESERVED + ".",
            "stack-exchange": "// https://stack" + J + "overflow.com/questions/1/x",
            "glibc": "/* from gli" + J + "bc e_exp.c */",
            "ibm-accurate": "/* IBM Accurate " + J + "Mathematical Library */",
            "ultimate-solution": "// Ultimate " + J + "Solution for exp",
        }.items():
            with self.subTest(rid):
                self.assertIn(rid, rules(line))

    def test_identifiers(self):
        for ident in ("GP" + "Lv3", "AG" + "PL-3.0", "GP" + "L-2.0-or-later", "EP" + "L-2.0", "CC BY-" + "SA 4.0",
                      "CC-BY-" + "NC-4.0", "OF" + "L-1.1"):
            with self.subTest(ident):
                self.assertEqual(rules("licence: " + ident), ["licence-id"])

    def test_near_misses(self):
        for line in ("gpl" + "aydl", "MIT License", "Apache-2.0", "MPL-2.0", "CC BY 4.0", "mod_license=" + RESERVED,
                     "a glib helper", "a GNU make target", "the stack overflowed"):
            with self.subTest(line):
                self.assertEqual(rules(line), [])

    def test_reserved_only_in_a_header(self):
        self.assertEqual(rules("# " + RESERVED), ["all-rights-reserved"])
        self.assertEqual(rules("the docs say " + RESERVED), [])

    def test_spdx(self):
        self.assertEqual(rules("// " + SPDX + "MIT OR Apache-2.0"), [])
        self.assertEqual(rules("// " + SPDX + "Sun" + "Pro"), ["spdx"])      # not on this repository's list
        self.assertEqual(rules("// " + SPDX + "Apache-2.0 WITH LLVM-exception"), [])
        self.assertEqual(rules("// " + SPDX + "LG" + "PL-2.1-only"), ["spdx"])


class WaysThrough(unittest.TestCase):
    def test_a_marker_needs_a_reason(self):
        self.assertEqual(rules("// " + LGPL + "  // " + ALLOW + " names the licence we turned down"), [])
        self.assertEqual(rules("// " + LGPL + "  // " + ALLOW), ["allow-needs-reason"])

    def test_exempt_files(self):
        for p in ("LICENSE", "THIRD_PARTY.md", "tools/licence-scan.py", "tools/port-publish.py", "tools/gen-vendored.py",
                  "tools/golden/port-publish-single-version.txt", ".claude/skills/publish-port/SKILL.md"):
            self.assertTrue(POLICY.exempt_path(p), p)
        for p in ("src/main/java/X.java", "tools/port.py", "docs/third_party.md", "CATALOG.md", "templates/x/LICENSE.txt",
                  "tools/golden/other.txt"):
            self.assertFalse(POLICY.exempt_path(p), p)
        self.assertEqual(rules("// " + LGPL, exempt=True), [])

    def test_an_exemption_without_a_reason_does_not_load(self):
        raw = json.loads((TOOLS / "licence-rules.json").read_text(encoding="utf-8"))
        raw["exempt"].append({"glob": "x/*"})
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "r.json").write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ValueError):
                ns["Policy"](Path(t) / "r.json")


class Register(unittest.TestCase):
    TABLE = "| file | source | licence | why |\n|---|---|---|---|\n| `v/a.js` | up | MIT | x |\n| `f/*.ttf` | y | PENDING #9 (OFL) | z |\n"

    def test_registered_and_not(self):
        rows = ns["register_rows"](self.TABLE)
        found, held = ns["audit_register"](POLICY, rows, ["v/a.js", "f/a.ttf", "b.c"], {"v/a.js": (1, COPR)}, "R")
        self.assertEqual((found, len(held)), ([], 1))
        found, _ = ns["audit_register"](POLICY, rows, ["v/a.js", "f/a.ttf", "b.c"], {"b.c": (1, COPR)}, "R")
        self.assertEqual([f[2] for f in found], ["copyright-unregistered"])

    def test_stale_and_off_list(self):
        rows = ns["register_rows"](self.TABLE + "| `gone` | s | MIT | w |\n| `c.c` | s | GP" + "L-3.0 | w |\n")
        found, _ = ns["audit_register"](POLICY, rows, ["v/a.js", "f/a.ttf", "c.c"], {}, "R")
        self.assertEqual(sorted(f[2] for f in found), ["register-licence", "register-stale"])

    def test_a_recorded_permission(self):
        rows = ns["register_rows"]("| `*.py` | someauthor | PERMISSION examplelib | the library |\n"
                                   "| `x.py` | someone | PERMISSION nobody | unrecorded |\n")
        raw = json.loads((TOOLS / "licence-rules.json").read_text(encoding="utf-8"))
        raw["permissions"] = {"examplelib": {"record": "a test's record"}}     # this repository records none yet
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "r.json").write_text(json.dumps(raw), encoding="utf-8")
            policy = ns["Policy"](Path(t) / "r.json")
        found, _ = ns["audit_register"](policy, rows, ["a.py", "x.py"], {}, "R")
        self.assertEqual([(f[2], "nobody" in f[3]) for f in found], [("register-licence", True)])
        found, _ = ns["audit_register"](POLICY, rows, ["a.py", "x.py"], {}, "R")
        self.assertEqual(len(found), 2, "with no recorded permission, every PERMISSION row is refused")

    def scoped_policy(self):
        raw = json.loads((TOOLS / "licence-rules.json").read_text(encoding="utf-8"))
        raw["scoped"] = {"Fooware": {"aliases": ["the fooware notice"], "globs": ["vendor/foo/*.c", "foo.NOTICE.txt"],
                                     "why": "a test's scoped approval"}}
        raw["permissions"] = {"bar": {"record": "a test's record", "globs": ["vendor/bar/*"]}}
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "r.json").write_text(json.dumps(raw), encoding="utf-8")
            return ns["Policy"](Path(t) / "r.json")

    def test_a_scoped_licence_holds_only_inside_its_globs(self):
        policy = self.scoped_policy()
        tracked = ["vendor/foo/a.c", "vendor/foo/b.c", "foo.NOTICE.txt", "src/x.c"]
        ok = ns["register_rows"]("| `vendor/foo/*.c` | foo | Fooware | ported |\n| `foo.NOTICE.txt` | foo | the fooware notice | n |\n")
        self.assertEqual(ns["audit_register"](policy, ok, tracked, {}, "R")[0], [])
        bad = ns["register_rows"]("| `src/x.c` | foo | Fooware | copied |\n| `*.c` | foo | Fooware | too wide |\n")
        found, _ = ns["audit_register"](policy, bad, tracked, {}, "R")
        self.assertEqual(sorted(f[2] for f in found), ["register-licence", "register-licence"])
        self.assertIsNone(POLICY.allowed_at("Fooware", ["vendor/foo/a.c"]), "the shipped rules allow it nowhere")

    def test_a_scoped_spdx_id_only_where_scoped(self):
        policy = self.scoped_policy()
        line = "// " + SPDX + "Fooware"
        self.assertEqual(ns["judge_line"](policy, "vendor/foo/a.c", 1, line, False), [])
        self.assertEqual([f[2] for f in ns["judge_line"](policy, "src/x.c", 1, line, False)], ["spdx"])

    def test_a_path_scoped_permission(self):
        policy = self.scoped_policy()
        tracked = ["vendor/bar/a.py", "src/b.py"]
        ok = ns["register_rows"]("| `vendor/bar/*` | bar | PERMISSION bar | by permission |\n")
        self.assertEqual(ns["audit_register"](policy, ok, tracked, {}, "R")[0], [])
        wide = ns["register_rows"]("| `*.py` | bar | PERMISSION bar | too wide |\n")
        self.assertEqual([f[2] for f in ns["audit_register"](policy, wide, tracked, {}, "R")[0]], ["register-licence"])

    def test_a_scoped_entry_needs_globs_and_a_why_and_is_not_also_general(self):
        raw = json.loads((TOOLS / "licence-rules.json").read_text(encoding="utf-8"))
        for bad in ({"X": {"globs": [], "why": "w"}}, {"X": {"globs": ["a"]}}, {"MIT": {"globs": ["a"], "why": "w"}}):
            raw["scoped"] = bad
            with tempfile.TemporaryDirectory() as t:
                (Path(t) / "r.json").write_text(json.dumps(raw), encoding="utf-8")
                with self.assertRaises(ValueError, msg=str(bad)):
                    ns["Policy"](Path(t) / "r.json")

    def test_our_own_notice(self):
        own = re.search(r"(?m)^(Copy" + J + r"right \(c\) \d{4} .+?)\s*$", (TOOLS.parent / "LICENSE").read_text(encoding="utf-8"))
        self.assertIsNotNone(own, "LICENSE has no copyright line")
        self.assertIsNone(POLICY.foreign_notice("# " + own.group(1)), "this repository's own notice (LICENSE) is not foreign")
        self.assertIsNotNone(POLICY.foreign_notice("# " + COPR + " Someone"))


def jar(entries):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for k, v in entries.items():
            z.writestr(k, v)
    return b.getvalue()


def pom(g, a, lic=None):
    lics = f"<licenses><license><name>{lic}</name></license></licenses>" if lic else ""
    return f'<project xmlns="http://maven.apache.org/POM/4.0.0"><groupId>{g}</groupId><artifactId>{a}</artifactId>{lics}</project>'


class Bundled(unittest.TestCase):
    def rows(self, inner):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "w.jar"
            p.write_bytes(jar({"META-INF/jarjar/lib.jar": jar({"META-INF/maven/g/a/pom.xml": inner})}))
            return ns["bundled_in_jar"](POLICY, p)

    def test_jar_licences(self):
        self.assertEqual(self.rows(pom("g", "a", "The Apache Software License, Version 2.0"))[0][2], "Apache-2.0")
        self.assertIsNone(self.rows(pom("g", "a", LGPL))[0][2])
        self.assertIsNone(self.rows(pom("g", "a"))[0][2])

    def test_lock_files(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "lock.json"
            p.write_text(json.dumps({"packs": {"a": {"license": "MIT", "version": "1"},
                                               "b": {"license": "CC-BY-" + "NC-4.0", "version": "2"}}}), encoding="utf-8")
            got = {r[0]: r[2] for r in ns["bundled_in_lock"](POLICY, p)}
            self.assertEqual(got, {"a": "MIT", "b": None})


def _writable(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)                        # .git/objects are read-only on Windows
    func(path)


class Hooks(unittest.TestCase):
    """The hooks tools/install-hooks.sh installs, in a repository that holds only what they run before this check."""
    NEEDS = ("install-hooks.sh", "python.sh", "check-no-ip.py", "licence-scan.py", "licence-rules.json")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "r"
        (self.repo / "tools").mkdir(parents=True)
        for name in self.NEEDS:
            shutil.copy2(TOOLS / name, self.repo / "tools" / name)
        (self.repo / "THIRD_PARTY.md").write_text("| file | source | licence | why |\n|---|---|---|---|\n", encoding="utf-8")
        self.env = {k: v for k, v in os.environ.items() if not k.endswith(("_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_B64"))}
        self.env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t",
                        GIT_COMMITTER_EMAIL="t@example.invalid", FORBIDDEN_NAMES_FILE="",
                        MMVU_SIMILARITY_STRICT="")
        self.git("init", "-q", "-b", "main")
        self.git("config", "core.autocrlf", "false")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "the tools")                  # before the hooks are on
        self.git("config", "core.hooksPath", ".git/hooks")           # not a hooks path from the user's global config
        bash = ns_bash()
        subprocess.run([bash, str(self.repo / "tools/install-hooks.sh")], capture_output=True, env=self.env, check=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, onerror=_writable)

    def git(self, *a, check=True):
        return subprocess.run(["git", "-C", str(self.repo), *a], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=self.env, check=check)

    def test_pre_commit_refuses_an_lgpl_header(self):
        (self.repo / "e_exp.c").write_text(HEADER, encoding="utf-8")
        self.git("add", "e_exp.c")
        r = self.git("commit", "-q", "-m", "port", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("[gnu-lgpl]", r.stderr + r.stdout)

    def test_pre_push_refuses_it_too(self):
        (self.repo / "e_exp.c").write_text(HEADER, encoding="utf-8")
        self.git("add", "e_exp.c")                                   # a commit that never met pre-commit (a
        tree = self.git("write-tree").stdout.strip()                 # cherry-pick, another clone): what pre-push
        sha = self.git("commit-tree", tree, "-p", "HEAD", "-m", "port").stdout.strip()   # is for
        self.git("update-ref", "refs/heads/main", sha)
        bare = self.tmp / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        self.git("remote", "add", "origin", str(bare))
        r = self.git("push", "-q", "origin", "main:refs/heads/feature", check=False)   # main itself is refused anyway
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("[gnu-lgpl]", r.stderr)
        self.assertEqual(subprocess.run(["git", "--git-dir", str(bare), "branch"], capture_output=True, text=True,
                                        encoding="utf-8").stdout, "")

    def test_a_clean_commit_and_a_message_that_names_a_licence(self):
        (self.repo / "ok.c").write_text("int g(void);\n", encoding="utf-8")
        self.git("add", "ok.c")
        r = self.git("commit", "-q", "-m", "keep code under the " + LGPL + " out", check=False)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=1)
