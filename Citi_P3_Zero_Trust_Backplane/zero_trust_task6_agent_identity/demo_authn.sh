#!/usr/bin/env bash
# AUTHN demo: (1) a legitimate agent identity passes, (2) impersonation is blocked before the PDP.
#
# Run from a checkout of this branch:  bash Citi_P3_Zero_Trust_Backplane/zero_trust_task6_agent_identity/demo_authn.sh
# Prerequisites (override with env vars if yours differ):
#   PY        python with the project deps installed   (default: citi_banking_adk/.venv/bin/python3, else python3)
#   MANIFEST  scanner output listing agents            (default: native_mas_runtime/output/task2/draft_manifests/agents_draft.yaml;
#                                                       produced by `run_cli.py --zero-trust --zt-refresh ...`)
#   DB        banking.db that already contains CUST-STRUCT (default: citi_banking_adk/bank_adk/.runtime/banking.db)
#   citi_banking_adk/.env must hold the model API key (never committed).
# Keys, the trust root, and the registry are generated fresh in $DEMO (outside the repo) and never committed.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BP="$(cd "$HERE/.." && pwd)"                         # Citi_P3_Zero_Trust_Backplane
ROOT="$(cd "$BP/.." && pwd)"                         # repo root
ADK="$ROOT/citi_banking_adk"
PY="${PY:-$([ -x "$ADK/.venv/bin/python3" ] && echo "$ADK/.venv/bin/python3" || echo python3)}"
MANIFEST="${MANIFEST:-$BP/native_mas_runtime/output/task2/draft_manifests/agents_draft.yaml}"
DB="${DB:-$ADK/bank_adk/.runtime/banking.db}"
DEMO="${DEMO:-${TMPDIR:-/tmp}/authn_demo_out}"
MSG="investigate CUST-STRUCT for potential structuring"
export PYTHONPATH="$HERE/src"

for f in "$MANIFEST" "$DB" "$ADK/.env"; do
  [ -f "$f" ] || { echo "Missing prerequisite: $f (see header of this script)"; exit 1; }
done

# 1. Fresh trust root (outside any repo)
rm -rf "$DEMO" && mkdir -p "$DEMO/keys" "$DEMO/runtime" && chmod 700 "$DEMO"
cp "$DB" "$DEMO/runtime/banking.db"
$PY - "$DEMO" <<'PYEOF'
import sys
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization as s
d = sys.argv[1]
k = Ed25519PrivateKey.generate()
open(f"{d}/root-private.pem", "wb").write(k.private_bytes(s.Encoding.PEM, s.PrivateFormat.PKCS8, s.NoEncryption()))
open(f"{d}/root-public.pem", "wb").write(k.public_key().public_bytes(s.Encoding.PEM, s.PublicFormat.SubjectPublicKeyInfo))
PYEOF
chmod 600 "$DEMO/root-private.pem"

# 2. AUTHN-1 + AUTHN-2: agent keys and a root-signed registry
echo "== AUTHN-1/2: generate agent keys + root-signed registry =="
(cd "$HERE" && $PY bootstrap_agent_keys.py --manifest "$MANIFEST" \
  --keys-dir "$DEMO/keys" --public-keys "$DEMO/public_keys.yaml" --registry "$DEMO/registry.yaml" \
  --registry-root-private-key "$DEMO/root-private.pem" --registry-root-key-id demo-root-v1 | tail -3)

# 3. Key maps: honest, and impersonation (aml_investigator holds sar_drafting_agent's key)
$PY - "$DEMO" <<'PYEOF'
import json, os, sys
d = sys.argv[1]
m = {f[:-4]: os.path.join(d, "keys", f) for f in os.listdir(f"{d}/keys") if f.endswith(".key")}
json.dump(m, open(f"{d}/keymap.json", "w"))
m["aml_investigator"] = m["sar_drafting_agent"]
json.dump(m, open(f"{d}/keymap_impersonation.json", "w"))
PYEOF

export BANKING_ADK_RUNTIME_DIR="$DEMO/runtime"
run() {  # $1 = keymap, $2 = output dir
  (cd "$ADK" && $PY run_cli.py --zero-trust --zt-backplane-root "$BP" --zt-output "$2" \
    --zt-agent-key-map "$1" --zt-identity-registry "$DEMO/registry.yaml" \
    --zt-identity-registry-root-public-key "$DEMO/root-public.pem" --zt-identity-registry-root-key-id demo-root-v1 \
    --message "$MSG" 2>&1 | grep -v context_cache_config \
    | $PY -c "import sys,json; d=json.load(sys.stdin); [print(e['agent'],'|',e['event'],'|',e.get('tool') or e.get('key') or e.get('to'),'|',(str(e.get('value',''))[:110] if e['event']=='result' else '')) for e in d]")
}
audit() { $PY - "$1/native_mas_audit.jsonl" <<'PYEOF'
import sys, json
for l in open(sys.argv[1]):
    r = json.loads(l)
    if r.get("event_type") == "TOOL_AUTHORIZATION" or "IDENTITY" in str(r.get("event_type")):
        print("  AUDIT", r["event_type"], r.get("caller_runtime_id"), "->", r.get("target_runtime_id"), "|", r.get("decision") or "", "|", r.get("reason") or "")
PYEOF
}

echo; echo "== RUN 1: legitimate identity =="; run "$DEMO/keymap.json" "$DEMO/out_ok"; audit "$DEMO/out_ok"
echo; echo "== RUN 2: IMPERSONATION (aml_investigator signs with another agent's key) =="; run "$DEMO/keymap_impersonation.json" "$DEMO/out_attack"; audit "$DEMO/out_attack"
echo; echo "Expected: RUN 1 shows TOOL_AUTHORIZATION (ALLOW). RUN 2 shows IDENTITY_VERIFICATION_FAILED and no TOOL_AUTHORIZATION."
