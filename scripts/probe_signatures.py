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

    content = api_get(f"/forms/content/{form_id}")
    if not content:
        print("  ERROR: no content returned")
        return

    if isinstance(content, str):
        content = json.loads(content)

    groups = content.get("Groups", [])
    print(f"  Groups: {[g.get('Title') for g in groups]}")

    for group in groups:
        title = group.get("Title", "")
        items = group.get("Items", [])
        for item in items:
            # Print any item that looks signature-related
            item_type = str(item.get("Type", "")).lower()
            content_val = str(item.get("Content", "")).lower()
            has_signer = item.get("SignerName") or item.get("signerName")
            is_sig_group = "signature" in title.lower()

            if is_sig_group or "signature" in item_type or has_signer:
                print(f"\n  [{title}] item:")
                print(json.dumps(item, indent=4, default=str))


if __name__ == "__main__":
    ids = sys.argv[1:] or [
        "ba9ac95f-f3f3-433e-bee0-2cd178c87ac1",
        "0fb3a1da-12f6-46e4-a131-6ad8c71dd0ab",
    ]
    for fid in ids:
        probe(fid)
