#!/usr/bin/env bash
# tools/burndown-count.sh -- the ONE trustworthy way to read an error count off a gradle log.
#
# A burn-down (§X6) is the instrument that makes a multi-thousand-error port a schedulable task
# instead of an open-ended one, and it is only worth anything if the number means the same thing
# at both ends. There are FOUR ways to read zero (or near-zero) and be wrong, every one of them
# in the reassuring direction, and each cost a real session before it was written down:
#
#   X5a  the build never reached compilation   -> no error: lines at all, grep -c prints 0
#   X5b  javac ABORTED AT PARSE                -> a broken file reads as a 99.8% burn-down
#   X13  the §W preprocessor failed            -> same silence, one task earlier
#   X5   gradle echoes compiler output twice   -> a halving that is only the counter
#
# So: assert the compile RAN, classify the errors by KIND, and count UNIQUE file:line.
#
#   ./gradlew compileJava -Pmc=26.2 --console=plain > /tmp/b.log 2>&1
#   tools/burndown-count.sh /tmp/b.log
#
# Exit 0 = a number you can quote. 2 = never compiled. 3 = no log. 4 = parse abort. 5 = javac crashed.
#   - X5a: a build that never reached compilation has 0 error: lines and that is NOT 0 errors.
#   - X5b: gradle echoes compiler output twice -> count UNIQUE file:line, not lines.
#   - X13: in a §W tree, prepareSources failing also yields 0 error: lines.
# Exits 0 with a number only when the compile genuinely ran.
LOG="$1"
[ -f "$LOG" ] || { echo "X13: no log at $LOG"; exit 3; }

# Did compileJava actually EXECUTE? (not merely appear in a failure message)
if grep -qE '^> Task :(compileJava|compileTestJava)( |$)' "$LOG"; then
  RAN=yes
elif grep -qE '^> Task :(compileJava|compileTestJava) (UP-TO-DATE|FROM-CACHE|NO-SOURCE)' "$LOG"; then
  RAN=skipped
else
  RAN=no
fi

if [ "$RAN" = no ]; then
  echo "NOT A COUNT -- compileJava never ran. Blocking failure:"
  grep -E "^(FAILURE|\* What went wrong|Could not resolve|Could not (GET|find)|A problem (was found|occurred)|> )" "$LOG" \
    | grep -v '^> Task' | head -8
  exit 2
fi

N=$(grep -oE '^[^ ]+\.java:[0-9]+: error:' "$LOG" | sort -u | wc -l | tr -d ' ')

# ⚠ Match the FAMILY "'X' expected", not a list of members. Enumerated, this guard missed
# "'(' expected" and reported 3 errors as if they were a count -- the very failure it exists
# for, one punctuation mark along.
# X5b: javac stops at PARSE when a file will not parse, so it never attributes and never sees
# the rest. Three parse errors in three files then read as a 99.8% burn-down. Distinguish by
# error KIND: parse errors are a broken file, not progress.
PARSE=$(grep -oE "^[^ ]+\.java:[0-9]+: error: ('[^']*' expected|<identifier> expected|illegal start of|class, interface, enum, or record expected|reached end of file while parsing|not a statement)" "$LOG" \
        | sed -E 's/: error:.*//' | sort -u | wc -l | tr -d ' ')
if [ "$N" -gt 0 ] && [ "$PARSE" -gt 0 ] && [ $((PARSE * 2)) -ge "$N" ]; then
  echo "PARSE ABORT -- $PARSE of $N errors are SYNTAX. javac stopped before attribution, so this"
  echo "is NOT a count: a rewrite broke a file. Offending files:"
  grep -oE "^[^ ]+\.java:[0-9]+: error: ('[^']*' expected|<identifier> expected|illegal start of|class, interface, enum, or record expected|reached end of file while parsing)" "$LOG" | sort -u | head -6
  exit 4
fi
# X5c: a FIFTH way to read zero. javac can die with an INTERNAL exception (a StackOverflowError
# attributing a huge generated expression is the one seen here), and it then prints a Java stack
# trace and NO `error:` line at all -- so a build that failed reports "errors = 0". The guard the
# other four share ("did compileJava run") does not catch it: it ran, and it crashed.
if [ "$N" -eq 0 ] && grep -qE 'An exception has occurred in the compiler' "$LOG"; then
  echo "COMPILER CRASH -- javac threw, so it printed no error: lines. This is NOT 0 errors:"
  grep -E 'An exception has occurred in the compiler|^java\.lang\.[A-Za-z]+' "$LOG" | head -3
  exit 5
fi
# X5d: the task can START and fail before javac runs -- an unresolvable dependency fails
# compileJava itself ("> Task :compileJava FAILED" + "Could not resolve"), which the RAN check
# above accepts. Zero error: lines from a FAILED compile task is not zero errors.
if [ "$N" -eq 0 ] && grep -qE '^> Task :(compileJava|compileTestJava) FAILED' "$LOG"; then
  echo "NOT A COUNT -- compileJava FAILED without a single javac error (it never compiled):"
  grep -E "^(\* What went wrong|> |   > )" "$LOG" | grep -v '^> Task' | head -4
  exit 2
fi
JAVAC=$(grep -oE '^[0-9]+ error(s)?$' "$LOG" | tail -1)
echo "errors = $N   (compileJava: $RAN${JAVAC:+, javac said: $JAVAC})"
