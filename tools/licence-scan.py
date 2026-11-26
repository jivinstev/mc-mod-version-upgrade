#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0. If a copy of the MPL
# was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
# Copied unchanged in substance from another project's tools/licence-scan.py; THIRD_PARTY.md names the source and
# what was changed here. This file stays MPL-2.0 inside this MIT repository (MPL-2.0 is file-level copyleft).
"""licence-scan.py: refuse third-party code or text whose licence is not on this repository's allow-list.

    python3 tools/licence-scan.py --tree             every tracked file, then the register (THIRD_PARTY.md)
    python3 tools/licence-scan.py --staged           the lines the index adds (pre-commit)
    python3 tools/licence-scan.py --range A..B       the lines these commits add (pre-push); takes any rev-list
                                                     arguments after --, e.g. -- TIP --not --remotes=origin
    python3 tools/licence-scan.py --bundled JAR... [--lock FILE...]
                                                     what we SHIP from others: every library inside a jar's
                                                     META-INF/jarjar/, and every row of a JSON lock file whose
                                                     entries carry a "license" field
    python3 tools/licence-scan.py --bundled --requirements requirements.txt
                                                     every Python package a user installs to run the compiler:
                                                     its licence from the rules' python_licences
    python3 tools/licence-scan.py PATH...            only these files (no register check)

    --rules FILE  (default: licence-rules.json beside this script)   --register FILE  (default: THIRD_PARTY.md)
    --repo DIR    the repository the git modes read (default: the one this script is in)

Exit 0 clean, 1 at least one finding, 2 the scan could not run. A hook treats 2 as a refusal.

What a line may not ADD (case-insensitive, word-bounded; the patterns live in the rules file):
  * licence wording or an identifier that is not on the allow-list: GNU GPL/LGPL/AGPL text, the Free Software
    Foundation, GPL/LGPL/AGPL/EPL/EUPL/CDDL/SSPL/OFL/CC BY-SA/NC/ND ids, an SPDX-License-Identifier naming anything
    off the list, or "All rights reserved" in a header (a comment line, or a line that also carries a copyright);
  * provenance: a source known to be copyleft, named the way ported code cites its origin (glibc and its IBM math
    library), and links to Stack Exchange answers (CC BY-SA);
  * a copyright notice that is not the project's own, in a file with no row in the register.
Commit messages are not scanned: they describe a change and carry no code.

Two ways through, both reviewable:
  * `licence-scan: allow <reason>` on the line itself. The reason is required.
  * an `exempt` glob in the rules file, with a reason: licence texts, notice files, the register, and documents that
    explain a licence. An exempt file skips the wording rules; its foreign copyright still needs a register row.

The register is a markdown table, `| `path or glob` | source | licence | why |`. The tree check fails a foreign
copyright with no row, a row that matches no tracked file, and a licence that is neither allow-listed,
`PERMISSION <name>` (code its author let us use without an open-source licence; each <name> is in the rules'
`permissions` with where that permission is recorded), nor `PENDING #<issue>` (held for a decision, printed as a
warning).

Approvals can be PATH-SCOPED, so a decision about one file never becomes a general code licence:
  * `scoped`: a licence allowed only for the files its `globs` match (an SPDX id or a register row anywhere else is
    refused as off the list);
  * a `permissions` entry with `globs` holds only for rows whose files all match them.
A register row passes a scoped approval only when every tracked file the row's path matches is inside its globs.

Standard library only; Python 3.9+; reads and prints UTF-8 on every platform. Copy it with its rules file.
"""
from __future__ import annotations

import argparse
import fnmatch
import io
import json
import pathlib
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

TOOLS = pathlib.Path(__file__).resolve().parent
BINARY = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".ogg", ".mp3", ".wav", ".mp4", ".webm", ".jar", ".zip",
          ".gz", ".tgz", ".xz", ".bz2", ".7z", ".nbt", ".mca", ".dat", ".class", ".ttf", ".otf", ".woff", ".woff2",
          ".pdf", ".bin"}
LIMIT = 5_000_000

MARK = re.compile(r"licence-scan:\s*allow\b[\s:,-]*(.*)", re.I)
COMMENTISH = re.compile(r"^\s*(?:#|//|/\*|\*|<!--|--|;|%|@?rem\b)", re.I)
NOTICE = re.compile(r"\bcopyright\b\s*:?\s*(?:\(c\)|©)?\s*(?=\d{4})|\bcopyright\s*(?:\(c\)|©)|©\s*(?=\d{4})", re.I)
SPDX_LINE = re.compile(r"SPDX-License-Identifier:\s*([^\n*>]+)", re.I)


def fold(text):
    return " ".join(text.lower().split()).rstrip(".")


class Policy:
    """The rules file, compiled."""

    def __init__(self, path):
        raw = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        self.names = {}
        for spdx, aliases in raw["allow"].items():
            for alias in [spdx, *aliases]:
                self.names[fold(alias)] = spdx
        self.rules = []
        for group in ("wording", "provenance"):
            for r in raw[group]:
                if not r.get("why"):
                    raise ValueError(f"rule {r.get('id')!r} needs a 'why'")
                self.rules.append((r["id"], re.compile(r["pattern"], re.I), r["why"], bool(r.get("header_only"))))
        self.own = [re.compile(p, re.I) for p in raw.get("own_copyright", [])]
        self.exempt = []
        for e in raw.get("exempt", []):
            if not str(e.get("why", "")).strip():
                raise ValueError(f"exempt glob {e.get('glob')!r} needs a 'why'")
            self.exempt.append(e["glob"])
        self.permissions = {k: v for k, v in raw.get("permissions", {}).items() if not k.startswith("_")}
        for k, v in self.permissions.items():
            if not str(v.get("record", "")).strip():
                raise ValueError(f"permission {k!r} needs a 'record' saying where the permission is written down")
            if "globs" in v and not (isinstance(v["globs"], list) and v["globs"]):
                raise ValueError(f"permission {k!r}: 'globs', when given, lists the paths it covers")
        self.scoped = {}                        # licence -> (globs, folded names)
        for spdx, v in raw.get("scoped", {}).items():
            if spdx.startswith("_"):
                continue
            if not (isinstance(v.get("globs"), list) and v["globs"]) or not str(v.get("why", "")).strip():
                raise ValueError(f"scoped licence {spdx!r} needs its 'globs' and a 'why'")
            if fold(spdx) in self.names:
                raise ValueError(f"scoped licence {spdx!r} is also on the general allow-list")
            self.scoped[spdx] = (list(v["globs"]), {fold(a) for a in [spdx, *v.get("aliases", [])]})
        self.python = {k.lower(): v for k, v in raw.get("python_licences", {}).items() if not k.startswith("_")}
        self.jar_licences = {k: v for k, v in raw.get("jar_licences", {}).items() if not k.startswith("_")}
        for k, v in self.jar_licences.items():
            if not self.allowed(v.get("licence")) or not v.get("source"):
                raise ValueError(f"jar_licences {k!r} needs an allowed licence and a source")

    def permission_ok(self, licence, files=None):
        """True when LICENCE reads `PERMISSION <name>`, <name> is a recorded permission in the rules, and (when the
        permission is path-scoped) every one of FILES is inside its globs."""
        m = re.match(r"PERMISSION\s+(\S+)", licence, re.I)
        if not (m and m.group(1) in self.permissions):
            return False
        globs = self.permissions[m.group(1)].get("globs")
        return globs is None or within(files or [], globs)

    def allowed(self, name):
        return self.names.get(fold(name)) if name else None

    def allowed_at(self, name, files):
        """The licence id NAME resolves to for FILES: a general allow, or a scoped one whose globs hold every file."""
        if not name:
            return None
        general = self.allowed(name)
        if general:
            return general
        for spdx, (globs, names) in self.scoped.items():
            if fold(name) in names and within(files, globs):
                return spdx
        return None

    def exempt_path(self, path):
        path = path.replace("\\", "/")
        base = path.rsplit("/", 1)[-1]
        return any(fnmatch.fnmatchcase(base if "/" not in g else path, g) for g in self.exempt)

    def foreign_notice(self, line):
        if NOTICE.search(line) and not MARK.search(line) and not any(p.search(line) for p in self.own):
            return line.strip()[:120]
        return None


def within(files, globs):
    """True when FILES is non-empty and every one of them matches one of GLOBS."""
    return bool(files) and all(any(f == g or fnmatch.fnmatchcase(f, g) for g in globs) for f in files)


def spdx_off_list(policy, expression, path=None):
    off, after_with = [], False
    for tok in (t for t in re.split(r"[\s()]+", expression) if t):
        if tok.upper() in ("AND", "OR"):
            continue
        if tok.upper() == "WITH":
            after_with = True
            continue
        if after_with:                          # an exception id: the licence it modifies is what is judged
            after_with = False
            continue
        if not (policy.allowed_at(tok, [path]) if path else policy.allowed(tok)):
            off.append(tok)
    return off


def judge_line(policy, path, n, line, exempt):
    m = MARK.search(line)
    if m:
        reason = m.group(1).strip().strip("*/#->").strip()
        return [] if reason else [(path, n, "allow-needs-reason", "a `licence-scan: allow` marker must give a reason")]
    if exempt:
        return []
    out = []
    spdx = SPDX_LINE.search(line)
    if spdx:
        off = spdx_off_list(policy, spdx.group(1), path)
        if off:
            out.append((path, n, "spdx", f"SPDX-License-Identifier names {', '.join(off)}, which is not on the allow-list"))
    header = bool(COMMENTISH.match(line) or NOTICE.search(line))
    for rid, rx, why, header_only in policy.rules:
        if (header_only and not header) or (spdx and rid == "licence-id"):
            continue
        if rx.search(line):
            out.append((path, n, rid, why))
    return out


def judge(policy, path, numbered):
    """(findings, first foreign notice or None) for [(line number, text)] of PATH."""
    exempt = policy.exempt_path(path)
    found, first = [], None
    for n, line in numbered:
        found += judge_line(policy, path, n, line, exempt)
        if first is None:
            notice = policy.foreign_notice(line)
            if notice:
                first = (n, notice)
    return found, first


# ── the register ─────────────────────────────────────────────────────────────────────────────

ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|([^|]*)\|([^|]*)\|([^|]*)\|")


def register_rows(text):
    return {m.group(1).strip(): tuple(c.strip() for c in m.groups()[1:])
            for m in map(ROW.match, text.splitlines()) if m}


def covered(rows, path):
    return any(path == key or fnmatch.fnmatchcase(path, key) for key in rows)


def audit_register(policy, rows, tracked, notices, name):
    found, held = [], []
    for key, (source, licence, why) in sorted(rows.items()):
        files = [t for t in tracked if t == key or fnmatch.fnmatchcase(t, key)]
        if not files:
            found.append((name, 0, "register-stale", f"`{key}` matches no tracked file: remove the row"))
        if re.match(r"PENDING\s+#\d+", licence, re.I):
            held.append(f"{key}: {licence}")
        elif re.match(r"PERMISSION\b", licence, re.I) and policy.permission_ok(licence, files or [key]):
            pass                                 # used by the author's recorded permission (the rules' `permissions`)
        elif not policy.allowed_at(re.sub(r"\s*\(.*\)\s*$", "", licence), files or [key]):
            found.append((name, 0, "register-licence", f"`{key}`: '{licence}' is not on the allow-list"
                                                       f"{' for these files' if policy.scoped or policy.permissions else ''}"))
        if not source or not why:
            found.append((name, 0, "register-incomplete", f"`{key}` needs a source and a why"))
    for path, (n, notice) in sorted(notices.items()):
        if not covered(rows, path):
            found.append((path, n, "copyright-unregistered", f"a copyright that is not ours ({notice!r}) and no {name} row"))
    return found, held


# ── reading ──────────────────────────────────────────────────────────────────────────────────

def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True).stdout


def text_of(path):
    if path.suffix.lower() in BINARY:
        return None
    try:
        if not path.is_file() or path.stat().st_size > LIMIT:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    return None if b"\0" in data[:8192] else data.decode("utf-8", "replace")


HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)")


def added_lines(diff):
    """{path: [(line number, text)]} for what a -U0 diff adds."""
    files, path, n = {}, None, 0
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            path = None
        elif line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
            if path is not None:
                files.setdefault(path, [])
        elif line.startswith("@@"):
            m = HUNK.match(line)
            n = int(m.group(1)) if m else 0
        elif path is not None and line.startswith("+"):
            files[path].append((n, line[1:]))
            n += 1
    return files


def judge_diff(policy, diff, label=""):
    found, notices = [], {}
    for path, numbered in added_lines(diff).items():
        if pathlib.PurePosixPath(path).suffix.lower() in BINARY:
            continue
        f, first = judge(policy, path, numbered)
        found += [(label + p, n, r, w) for p, n, r, w in f]
        if first and path not in notices:
            notices[path] = first
    return found, notices


def judge_range(policy, repo, revs):
    cut = "\x1e"
    out = subprocess.run(["git", "-C", str(repo), "log", "--no-color", "--no-renames", "--no-ext-diff",
                          "--no-textconv", "-p", "-U0", f"--format={cut}%H", *revs], capture_output=True)
    if out.returncode:
        raise RuntimeError(out.stderr.decode("utf-8", "replace").strip())
    found, notices, commits = [], {}, 0
    for chunk in out.stdout.decode("utf-8", "replace").split(cut)[1:]:
        commits += 1
        sha, _, diff = chunk.partition("\n")
        f, nt = judge_diff(policy, diff, sha[:12] + ":")
        found += f
        for p, v in nt.items():
            notices.setdefault(p, v)
    return commits, found, notices


# ── what we ship from others ─────────────────────────────────────────────────────────────────

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def pom_facts(data):
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None, []
    kids = {_local(c.tag): c for c in root}
    group = (kids["groupId"].text if "groupId" in kids else None) or next(
        ((c.text or "") for p in root if _local(p.tag) == "parent" for c in p if _local(c.tag) == "groupId"), "")
    artifact = kids["artifactId"].text if "artifactId" in kids else ""
    names = [(c.text or "").strip() for el in root.iter() if _local(el.tag) == "license"
             for c in el if _local(c.tag) == "name"]
    return f"{(group or '').strip()}:{(artifact or '').strip()}", names


def bundled_in_jar(policy, jar):
    """[(what, version, licence or None, how it was read)] for every jar in JAR's META-INF/jarjar/."""
    rows = []
    with zipfile.ZipFile(jar) as z:
        meta = {}
        if "META-INF/jarjar/metadata.json" in z.namelist():
            for j in json.loads(z.read("META-INF/jarjar/metadata.json").decode("utf-8")).get("jars", []):
                i = j.get("identifier", {})
                meta[j.get("path")] = (f"{i.get('group')}:{i.get('artifact')}", j.get("version", {}).get("artifactVersion", "?"))
        for name in sorted(n for n in z.namelist() if n.startswith("META-INF/jarjar/") and n.endswith(".jar")):
            coords, version = meta.get(name, (None, "?"))
            said = []
            with zipfile.ZipFile(io.BytesIO(z.read(name))) as inner:
                for p in inner.namelist():
                    if p.startswith("META-INF/maven/") and p.endswith("/pom.xml"):
                        c, names = pom_facts(inner.read(p))
                        coords = coords or c
                        said += names
            ok = next((policy.allowed(s) for s in said if policy.allowed(s)), None)
            if ok:
                rows.append((coords, version, ok, "its POM"))
            elif coords in policy.jar_licences:
                v = policy.jar_licences[coords]
                rows.append((coords, version, policy.allowed(v["licence"]), "jar_licences: " + v["source"]))
            else:
                rows.append((coords or name, version, None, ", ".join(said) or "no licence in its POM, no jar_licences row"))
    return rows


def bundled_in_requirements(policy, req):
    rows = []
    for line in pathlib.Path(req).read_text(encoding="utf-8").splitlines():
        name = re.split(r"[\s;<>=!~\[#]", line.strip(), maxsplit=1)[0].lower()
        if not name:
            continue
        row = policy.python.get(name)
        lic = policy.allowed(row["licence"]) if row else None
        rows.append((name, "", lic, ("python_licences: " + row["source"]) if row else "no python_licences row"))
    return rows


def bundled_in_lock(policy, lock):
    data = json.loads(pathlib.Path(lock).read_text(encoding="utf-8"))
    entries = data.get("packs", data) if isinstance(data, dict) else {}
    rows = []
    for key, row in sorted(entries.items()):
        if isinstance(row, dict) and not key.startswith("_"):
            lic = row.get("license") or row.get("licence") or ""
            rows.append((key, row.get("version", "?"), policy.allowed(lic), lic or "no license field"))
    return rows


# ── main ─────────────────────────────────────────────────────────────────────────────────────

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*")
    how = ap.add_mutually_exclusive_group()
    for flag in ("--tree", "--staged", "--range", "--bundled"):
        how.add_argument(flag, action="store_true")
    ap.add_argument("--lock", nargs="*", default=[])
    ap.add_argument("--requirements", nargs="*", default=[])
    ap.add_argument("--rules", default=str(TOOLS / "licence-rules.json"))
    ap.add_argument("--register", default="THIRD_PARTY.md")
    ap.add_argument("--repo", default=str(TOOLS.parent))
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        policy = Policy(a.rules)
    except (OSError, ValueError, KeyError) as e:
        print(f"licence-scan: cannot load {a.rules}: {e}", file=sys.stderr)
        return 2
    repo = pathlib.Path(a.repo).resolve()
    reg = repo / a.register
    found, held, what = [], [], ""
    try:
        if a.bundled:
            for label, rows in [(j, bundled_in_jar(policy, j)) for j in a.paths] + \
                               [(l, bundled_in_lock(policy, l)) for l in a.lock] + \
                               [(r, bundled_in_requirements(policy, r)) for r in a.requirements]:
                print(f"{label}: {len(rows)} bundled")
                for item, version, lic, how_read in rows:
                    print(f"  {item} {version}: {lic or 'REFUSED'} ({how_read})")
                    if not lic:
                        found.append((label, 0, "bundled-licence", f"{item} {version}: {how_read}; not on the allow-list"))
            what = f"{len(a.paths)} jar(s), {len(a.lock)} lock file(s), {len(a.requirements)} requirements file(s)"
        elif a.staged or a.range:
            if a.staged:
                found, notices = judge_diff(policy, git(repo, "diff", "--cached", "--no-color", "--no-renames",
                                                        "--no-ext-diff", "--no-textconv", "-U0",
                                                        "--diff-filter=ACMRT").decode("utf-8", "replace"))
                what = "the staged changes"
            else:
                revs = [r for r in a.paths if r != "--"]
                if not revs:
                    print("licence-scan: --range needs revisions, e.g. A..B", file=sys.stderr)
                    return 2
                count, found, notices = judge_range(policy, repo, revs)
                what = f"{count} commit(s)"
            rows = register_rows(reg.read_text(encoding="utf-8")) if reg.exists() else {}
            tracked = set(git(repo, "ls-files", "-z").decode("utf-8", "replace").split("\0"))
            for path, (n, notice) in sorted(notices.items()):
                if a.range:                      # history: judge the file as it stands now, if it still exists
                    now = text_of(repo / path) if path in tracked else None
                    if now is None or judge(policy, path, enumerate(now.splitlines(), 1))[1] is None:
                        continue
                if not covered(rows, path):
                    found.append((path, n, "copyright-unregistered", f"adds a copyright that is not ours ({notice!r}) "
                                                                     f"and {a.register} has no row for it"))
        else:
            tracked = [p for p in git(repo, "ls-files", "-z").decode("utf-8", "replace").split("\0") if p]
            notices = {}
            for rel in a.paths or tracked:
                text = text_of(repo / rel)
                if text is not None:
                    f, first = judge(policy, rel, enumerate(text.splitlines(), 1))
                    found += f
                    if first:
                        notices[rel] = first
            if a.tree:
                if reg.exists():
                    f, held = audit_register(policy, register_rows(reg.read_text(encoding="utf-8")), tracked, notices,
                                             a.register)
                    found += f
                else:
                    found.append((a.register, 0, "register-missing", "the third-party register does not exist"))
            what = f"{len(a.paths or tracked)} file(s)" + (" and the register" if a.tree else "")
    except (subprocess.CalledProcessError, RuntimeError, OSError, ValueError, zipfile.BadZipFile) as e:
        msg = e.stderr.decode("utf-8", "replace").strip() if isinstance(e, subprocess.CalledProcessError) else str(e)
        print(f"licence-scan: could not run: {msg}", file=sys.stderr)
        return 2
    for where, n, rule, why in sorted(set(found)):
        print(f"{where}:{n}: [{rule}] {why}")
    for h in held:
        print(f"licence-scan: held for a decision: {h}", file=sys.stderr)
    print(f"licence-scan: {what}, {len(set(found))} finding(s)", file=sys.stderr)
    if found:
        print("licence-scan: code or text from someone else comes in only under an allow-listed licence; anything else "
              "stops and goes to the maintainers first. A line that only names a licence may carry "
              "`licence-scan: allow <reason>`.", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
