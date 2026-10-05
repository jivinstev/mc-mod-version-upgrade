#!/usr/bin/env python3
"""List and read jar/zip files without `unzip`, which Git for Windows does not ship.

The flags are unzip's, so every `unzip ...` line in the skills and the catalogue works unchanged
(on Windows, setup puts an `unzip` in ~/bin that runs this):
    zipls.py -l JAR                 listing: Length, Date, Time, Name (`| awk '{print $4}'` = names)
    zipls.py -Z1 JAR                names only, one per line
    zipls.py -p JAR MEMBER...       a member's bytes to stdout (MEMBER may be a glob: '*.mixins.json')
    zipls.py [-o] [-q] JAR [MEMBER...] [-d DIR]   extract (all, or the matching members)
Standard library only.
"""
import datetime, fnmatch, os, pathlib, sys, zipfile


def usage():
    print(__doc__.strip(), file=sys.stderr)
    return 10


def pick(names, patterns):
    if not patterns:
        return list(names)
    return [n for n in names if any(fnmatch.fnmatchcase(n, p) for p in patterns)]


def main(argv):
    # LF and UTF-8 on every OS: Windows text stdout writes CRLF, and `| grep -c '\\.class$'` then
    # matches nothing.
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    mode, quiet, dest, rest = "x", False, ".", []
    it = iter(argv)
    for a in it:
        if a == "-l":
            mode = "l"
        elif a == "-Z1":
            mode = "Z1"
        elif a == "-p":
            mode = "p"
        elif a == "-q" or a == "-qq":
            quiet = True
        elif a in ("-o", "-n"):
            pass                                  # always overwrite: the callers re-extract into scratch dirs
        elif a == "-d":
            dest = next(it, None)
            if dest is None:
                return usage()
        elif a.startswith("-") and len(a) > 1:
            print(f"zipls: unsupported option {a}", file=sys.stderr)
            return usage()
        else:
            rest.append(a)
    if not rest:
        return usage()
    jar, patterns = rest[0], rest[1:]
    try:
        zf = zipfile.ZipFile(jar)
    except (OSError, zipfile.BadZipFile) as e:
        print(f"zipls: cannot open {jar}: {e}", file=sys.stderr)
        return 9
    with zf:
        infos = zf.infolist()
        chosen = set(pick([i.filename for i in infos], patterns))
        if patterns and not chosen:
            print(f"caution: filename not matched:  {' '.join(patterns)}", file=sys.stderr)
            return 11
        out = sys.stdout
        if mode == "Z1":
            for i in infos:
                if i.filename in chosen:
                    out.write(i.filename + "\n")
        elif mode == "l":
            out.write(f"Archive:  {jar}\n  Length      Date    Time    Name\n---------  ---------- -----   ----\n")
            total = count = 0
            for i in infos:
                if i.filename not in chosen:
                    continue
                d = datetime.datetime(*i.date_time)
                out.write(f"{i.file_size:>9}  {d:%Y-%m-%d %H:%M}   {i.filename}\n")
                total += i.file_size
                count += 1
            out.write(f"---------                     -------\n{total:>9}                     "
                      f"{count} file{'s' if count != 1 else ''}\n")
        elif mode == "p":
            out.flush()
            for i in infos:
                if i.filename in chosen and not i.is_dir():
                    sys.stdout.buffer.write(zf.read(i))
        else:
            root = pathlib.Path(dest).resolve()
            if not quiet:
                out.write(f"Archive:  {jar}\n")
            for i in infos:
                if i.filename not in chosen:
                    continue
                target = (root / i.filename).resolve()
                if root != target and root not in target.parents:
                    print(f"zipls: skipped {i.filename}: it points outside {root}", file=sys.stderr)
                    continue
                zf.extract(i, root)
                if not quiet:
                    out.write(f"  inflating: {os.path.relpath(target)}\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except BrokenPipeError:                       # `| head` closed the pipe: not an error
        sys.exit(0)
