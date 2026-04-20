"""
scripts/probe_signatures.py
Fetches raw form content for given form IDs and dumps signature-related data.

Usage:
    python scripts/probe_signatures.py <form_id1> <form_id2> ...
"""
import sys, json, os
try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from etl.utils import api_get

def probe(form_id: str):
    print(f"\n{'='*60}")
    print(f"FORM: {form_id}")
    print(f"{'='*60}")

    # Check signatures endpoint filtered by formId
    sigs = api_get("/signatures", params={"formId": form_id})
    print(f"\n  /signatures?formId={form_id}:")
    print(json.dumps(sigs, indent=2, default=str))


if __name__ == "__main__":
    ids = sys.argv[1:] or [
        "ba9ac95f-f3f3-433e-bee0-2cd178c87ac1",
        "0fb3a1da-12f6-46e4-a131-6ad8c71dd0ab",
    ]
    for fid in ids:
        probe(fid)
