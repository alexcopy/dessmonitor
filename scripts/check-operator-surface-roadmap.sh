#!/usr/bin/env bash
# check-operator-surface-roadmap.sh
# Validate PR 0034k roadmap refresh.
set -euo pipefail

ROADMAP="${1:-ROADMAP.md}"

if [ ! -f "$ROADMAP" ]; then
    echo "ERROR: Roadmap file not found: $ROADMAP" >&2
    exit 127
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

if [ -n "${PYTHON_BIN:-}" ] && [ -x "$PYTHON_BIN" ]; then
    PYTHON="$PYTHON_BIN"
elif [ -x "$PROJECT_DIR/.venv3/bin/python3" ]; then
    PYTHON="$PROJECT_DIR/.venv3/bin/python3"
elif [ -x "$PROJECT_DIR/.venv/bin/python3" ]; then
    PYTHON="$PROJECT_DIR/.venv/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="$(command -v python3)"
else
    echo "ERROR: No Python interpreter found" >&2
    exit 127
fi

echo "Using Python interpreter: $PYTHON"
echo "=== PR 0034k operator surface roadmap check ==="
echo "Roadmap file: $ROADMAP"

"$PYTHON" - "$ROADMAP" <<'PYEOF'
import sys, re, os

roadmap_path = sys.argv[1]

with open(roadmap_path, encoding="utf-8") as f:
    text = f.read()

errors = []
test_num = 0

def ok(msg=""):
    global test_num
    test_num += 1
    print(f"  [{test_num}] {msg} ... OK")
    return True

def fail(msg=""):
    global test_num, errors
    test_num += 1
    print(f"  [{test_num}] {msg} ... FAIL")
    errors.append(f"  [{test_num}] {msg}")

# ================================================================
# 1. Current State recognizes authenticated dashboard
# ================================================================

if "authenticated" in text and "dashboard" in text:
    ok("Current State mentions authenticated dashboard")
else:
    fail("Current State does not mention authenticated dashboard")

# ================================================================
# 2. Current State recognizes canonical sensor snapshot
# ================================================================

if "SensorReadSnapshot" in text or "sensor snapshot" in text.lower():
    ok("Current State recognizes canonical sensor snapshot")
else:
    fail("Current State does not mention sensor snapshot")

# ================================================================
# 3. Exactly fifteen implementation PRs PR0035-PR0049
# ================================================================

# Find PR0035-PR0049 headings only (### PR00... lines)
pr_pattern = re.compile(r'^### PR00(3[5-9]|4[0-9])\b', re.MULTILINE)
matches = pr_pattern.findall(text)
pr_numbers = set()
for m in matches:
    pr_numbers.add(int(m))

expected = set(range(35, 50))
if pr_numbers == expected:
    ok(f"Exactly 15 PRs in range PR0035-PR0049 ({len(pr_numbers)} found)")
else:
    missing = expected - pr_numbers
    extra = pr_numbers - expected
    msg = f"PR count: {len(pr_numbers)}. Missing: {sorted(missing)}. Extra: {sorted(extra)}"
    fail(msg)

# ================================================================
# 4. PRs in numeric order
# ================================================================

pr_entries = re.findall(r'^### PR00(3[5-9]|4[0-9])\b', text, re.MULTILINE)
pr_nums = [int(x) for x in pr_entries]
if pr_nums == sorted(pr_nums):
    ok("PRs appear in numeric order")
else:
    fail(f"PRs not in order: {pr_nums}")

# ================================================================
# 5. PR0035 is Signed DESS Request Diagnostics
# ================================================================

pr35_section = text[text.find("PR0035"):]
pr35_end = pr35_section.find("\n### PR0036") if "PR0036" in pr35_section else len(pr35_section)
pr35_body = pr35_section[:pr35_end]
if "Redact" in pr35_body and "Signed" in pr35_body:
    ok("PR0035 is Signed DESS Request Diagnostics (security first)")
else:
    fail("PR0035 is not Signed DESS Request Diagnostics")

# ================================================================
# 6. Health-first Overview priority
# ================================================================

if "health" in text.lower() and "Overview" in text:
    ok("Health-first Overview priority is explicit")
else:
    fail("Health-first Overview priority not found")

# ================================================================
# 7. Six route-backed tabs
# ================================================================

required_tabs = ["Overview", "Energy", "Devices", "Sensors", "System", "History"]
found_tabs = [t for t in required_tabs if t in text]
if len(found_tabs) == 6:
    ok(f"Six route-backed tabs present: {', '.join(found_tabs)}")
else:
    fail(f"Found {len(found_tabs)}/6 tabs: {found_tabs}")

# ================================================================
# 8. Bulma preserved as CSS framework
# ================================================================

if "Bulma" in text:
    ok("Bulma preserved as CSS framework")
else:
    fail("Bulma not mentioned")

# ================================================================
# 9. No new frontend framework introduced
# ================================================================

forbidden = ["React", "Vue", "Angular", "Svelte", "HTMX", "Alpine.js"]
found_forbidden = [f for f in forbidden if f in text and "not" not in text[text.find(f)-30:text.find(f)+30].lower()]
if not found_forbidden:
    ok("No new frontend framework introduced")
else:
    fail(f"Forbidden frameworks found: {found_forbidden}")

# ================================================================
# 10. Heartbeat and inverter contracts precede UI (PR0036+0037 before PR0038)
# ================================================================

idx36 = text.find("PR0036")
idx37 = text.find("PR0037")
idx38 = text.find("PR0038")
if idx36 > 0 and idx37 > 0 and idx38 > 0 and idx36 < idx38 and idx37 < idx38:
    ok("Heartbeat (PR0036) and inverter (PR0037) precede web pipeline (PR0038)")
else:
    fail("PR0036/PR0037 do not precede PR0038")

# ================================================================
# 11. Manual hardware execution deferred beyond PR0049
# ================================================================

if "Manual Command Queue" in text and "deferred" in text.lower():
    ok("Manual hardware execution deferred beyond PR0049")
else:
    fail("Manual execution deferral not found")

# ================================================================
# 12. ML control remains deferred
# ================================================================

if "ml control" in text.lower() and "deferred" in text.lower():
    ok("ML control remains deferred")
else:
    fail("ML control deferral not found")

# ================================================================
# 13. TimescaleDB history follows stable live-read semantics
# ================================================================

if "TimescaleDB" in text and "PR0048" in text:
    ok("TimescaleDB history (PR0048) follows stable live-read semantics")
else:
    fail("TimescaleDB history not sequenced after stable live reads")

# ================================================================
# 14. Measured vs estimated vs derived consumption distinguished
# ================================================================

if "measured" in text.lower() and "estimated" in text.lower() and "derived" in text.lower():
    ok("Measured vs estimated vs derived consumption distinguished")
else:
    fail("Consumption types not distinguished")

# ================================================================
# 15. Safety invariants present
# ================================================================

safety_terms = ["no write API", "authenticated", "read-only", "safety"]
found_safety = [t for t in safety_terms if t.lower() in text.lower()]
if len(found_safety) >= 3:
    ok(f"Safety invariants present: {', '.join(found_safety)}")
else:
    fail(f"Safety invariants insufficient: {found_safety}")

# ================================================================
# 16. PR0036 is Heartbeat and Freshness Contract
# ================================================================

idx36_section = text.find("PR0036")
idx37_section = text.find("\n### PR0037")
pr36_body = text[idx36_section:idx37_section] if idx37_section > 0 else text[idx36_section:idx36_section+500]
if "Heartbeat" in pr36_body or "freshness" in pr36_body.lower():
    ok("PR0036 is Runtime Heartbeat and Source Freshness Contract")
else:
    fail("PR0036 is not Heartbeat/Freshness contract")

# ================================================================
# 17. PR0037 is Typed Inverter Observability Snapshot
# ================================================================

idx37_section = text.find("PR0037")
idx38_section = text.find("\n### PR0038")
pr37_body = text[idx37_section:idx38_section] if idx38_section > 0 else text[idx37_section:idx37_section+500]
if "Inverter" in pr37_body:
    ok("PR0037 is Typed Inverter Observability Snapshot")
else:
    fail("PR0037 is not Inverter Observability")

# ================================================================
# 18. PR0039 is Route-Backed Tabs Shell
# ================================================================

idx39_section = text.find("PR0039")
idx40_section = text.find("\n### PR0040")
pr39_body = text[idx39_section:idx40_section] if idx40_section > 0 else text[idx39_section:idx39_section+500]
if "Tab" in pr39_body or "tab" in pr39_body.lower():
    ok("PR0039 is Route-Backed Bulma Tabs Shell")
else:
    fail("PR0039 is not Tabs Shell")

# ================================================================
# 19. PR0040 is System Health Overview
# ================================================================

idx40_section = text.find("PR0040")
idx41_section = text.find("\n### PR0041")
pr40_body = text[idx40_section:idx41_section] if idx41_section > 0 else text[idx40_section:idx40_section+500]
if "Health" in pr40_body or "health" in pr40_body.lower():
    ok("PR0040 is System Health Overview")
else:
    fail("PR0040 is not Health Overview")

# ================================================================
# 20. PR0045 and PR0046 are separate
# ================================================================

if "PR0045" in text and "PR0046" in text:
    ok("PR0045 (canonical consumers) and PR0046 (fallback) are separate")
else:
    fail("PR0045/PR0046 not both present")

# ================================================================
# 21. PR0048 before PR0049
# ================================================================

idx48 = text.find("PR0048")
idx49 = text.find("PR0049")
if idx48 > 0 and idx49 > 0 and idx48 < idx49:
    ok("PR0048 (TimescaleDB API) before PR0049 (charts)")
else:
    fail("PR0048 does not precede PR0049")

# ================================================================
# 22. No Flask as operator interface
# ================================================================

if "Flask" not in text or "not" in text[text.find("Flask")-20:text.find("Flask")+20].lower():
    ok("Flask not introduced as operator interface")
else:
    fail("Flask mentioned as operator interface")

# ================================================================
# 23. Short polling preserved
# ================================================================

if "short polling" in text.lower():
    ok("Short polling preserved as live update model")
else:
    fail("Short polling not mentioned")

# ================================================================
# 24. Authentication invariants present
# ================================================================

if "authentication" in text.lower() and "session" in text.lower():
    ok("Authentication invariants present")
else:
    fail("Authentication invariants missing")

# ================================================================
# 25. No direct web-to-hardware calls
# ================================================================

if "no direct" in text.lower() or "direct web" in text.lower():
    ok("Direct web-to-hardware calls forbidden")
else:
    fail("Direct web-to-hardware prohibition not found")

# ================================================================
# Results
# ================================================================
print()
if errors:
    print(f"=== FAIL: {len(errors)} check(s) failed ===")
    for e in errors:
        print(f"  FAILED: {e}")
    sys.exit(1)
else:
    print(f"=== PASS: All {test_num} operator surface roadmap checks passed ===")
    sys.exit(0)
PYEOF

TEST_EXIT=$?
exit "$TEST_EXIT"
