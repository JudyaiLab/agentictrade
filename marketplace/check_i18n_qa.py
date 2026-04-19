"""QA script to check zh-tw translations in i18n.py"""
import re

with open('marketplace/i18n.py', 'r') as f:
    content = f.read()

# Parse all translation keys with en and zh-tw values
pattern = r'"([^"]+)":\s*\{[^}]*?"en":\s*"([^"]*?)"[^}]*?"zh-tw":\s*"([^"]*?)"'
matches = re.findall(pattern, content, re.DOTALL)

issues = []

for key, en, zh in matches:
    # Check 1: Empty zh-tw
    if not zh.strip():
        issues.append(('EMPTY', key, en, zh))

    # Check 2: zh-tw same as English (untranslated)
    if zh.strip() == en.strip() and len(en) > 3:
        skip_prefixes = ['lang.', 'docs.sdk_code', 'docs.example_']
        skip = any(key.startswith(p) for p in skip_prefixes)
        skip = skip or 'pip install' in en or 'python ' in en
        skip = skip or 'http' in en or '@' in en
        skip = skip or en.startswith('$') or en.startswith('#')
        if not skip:
            issues.append(('UNTRANSLATED', key, en, zh))

    # Check 3: Inconsistent terminology
    # month7 keys should reference month 4+ not month 7
    if 'month7' in key and '7' in zh:
        issues.append(('MONTH_REF_ERROR', key, en, zh))

print(f"Total zh-tw entries: {len(matches)}")
print(f"Issues found: {len(issues)}")
print("=" * 80)
for t, k, e, z in issues:
    print(f"[{t}] {k}")
    print(f"  EN: {e[:120]}")
    print(f"  ZH: {z[:120]}")
    print()

# Check for keys missing zh-tw entirely
all_keys_pattern = r'"([^"]+)":\s*\{'
all_keys = re.findall(all_keys_pattern, content)
# Filter to actual translation keys (have "en" inside)
trans_keys_pattern = r'"([^"]+)":\s*\{[^}]*?"en":\s*"'
trans_keys = set(re.findall(trans_keys_pattern, content, re.DOTALL))

zhtw_keys = set(k for k, _, _ in matches)
missing = trans_keys - zhtw_keys
if missing:
    print(f"\nKeys MISSING zh-tw translation ({len(missing)}):")
    for k in sorted(missing):
        print(f"  {k}")
else:
    print(f"\nNo keys missing zh-tw translation. All {len(trans_keys)} keys have zh-tw.")
