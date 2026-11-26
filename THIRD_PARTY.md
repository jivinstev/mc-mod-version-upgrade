# Third-party files

This repository is MIT ([LICENSE](LICENSE)). Every committed file that carries a copyright that is not ours, or is
copied from someone else's work, has a row here: where it came from, its licence and why it is here.
`tools/licence-scan.py --tree` (CI and the hooks `tools/install-hooks.sh` installs) fails a foreign copyright in a file
with no row, a row that matches no tracked file, and a licence that is not on the allow-list in
`tools/licence-rules.json`. A path may be a glob. A licence that reads `PENDING #<issue>` is a decision held for the
maintainer: the scan prints it and passes. `PERMISSION <name>` would mean code its author lets us use without an
open-source licence, recorded in the rules' `permissions`; none is recorded.

The licence scanner is MPL-2.0, which is file-level copyleft: its files stay MPL-2.0 here (each says so in its header),
a change to them is MPL-2.0 too, and nothing else in this repository is affected. The source is the maintainer's
own MPL-2.0 project's `tools/` folder, at commit `a8e78deb6c2ba24f94cbeb3aade01cc8e2627e52` (this repository names no
mod repository, so the project is not named here).

| file | source | licence | why |
|---|---|---|---|
| `tools/licence-scan.py` | the source above, `tools/licence-scan.py`; the MPL-2.0 notice added at the top, nothing else changed | MPL-2.0 | the licence check of the IP gate (pre-commit, pre-push, CI) |
| `tools/test-licence-scan.py` | the source above, `tools/test-licence-scan.py`; the MPL-2.0 notice added, the exemption, permission and own-notice cases and the hook tests adapted to this repository's rules and `tools/install-hooks.sh`, `encoding="utf-8"` on its git calls | MPL-2.0 | the scanner's self-tests (`tools/test-gates.sh`) |
| `tools/licence-rules.json` | the source above, `tools/licence-rules.json`: its allow-list, wording and provenance patterns kept, everything about the source repository removed, this repository's exemptions, scoped approvals and own copyright written here | MPL-2.0 | the rules the scanner enforces |
| `templates/*/gradlew` | Gradle wrapper script, github.com/gradle/gradle | Apache-2.0 | the template builds' wrapper |
| `templates/*/gradlew.bat` | Gradle wrapper script, github.com/gradle/gradle | Apache-2.0 | the template builds' wrapper on Windows |
| `templates/*/gradle/wrapper/gradle-wrapper.jar` | Gradle wrapper, github.com/gradle/gradle | Apache-2.0 | downloads the pinned Gradle (allowed by sha1 in `tools/check-no-ip.py`) |
