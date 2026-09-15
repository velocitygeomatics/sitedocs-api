"""
etl/sync_certifications.py
Syncs:
  - certifications
  - certification_attachments
"""

import logging
from .utils import as_utc, paginate, upsert, get_last_sync, set_last_sync, now_iso

log = logging.getLogger(__name__)


def sync_certifications(conn, incremental: bool = True):
    log.info("Syncing certifications...")

    params = {}
    if incremental:
        since = get_last_sync(conn, "certifications")
        if since:
            # No direct submittedSince on certifications API,
            # but we can still do a full sync — it's fast with upsert
            log.info(f"  incremental mode (last sync: {since})")

    rows = paginate("/certifications", extra_params=params)

    certs = []
    attachments = []

    for r in rows:
        certs.append({
            "id":                        r["Id"],
            "certification_type_id":     r.get("CertificationTypeId"),
            "certification_type_name":   r.get("CertificationTypeName"),
            "issuer":                    r.get("Issuer"),
            "ticket":                    r.get("Ticket"),
            "worker_id":                 r.get("WorkerId"),
            "acquired":                  r.get("Acquired"),
            "expires":                   r.get("Expires"),
            "acknowledged_expiry_date":  r.get("AcknowledgedExpiryDate"),
            "is_archived":               r.get("IsArchived", False),
            "created_on":                as_utc(r.get("CreatedOn")),
            "last_modified_on":          as_utc(r.get("LastModifiedOn")),
            "etl_synced_on":             now_iso(),
        })

        # Flatten nested attachments array
        for att in r.get("Attachments") or []:
            attachments.append({
                "id":               att.get("Id") or att.get("AttachmentId"),
                "certification_id": r["Id"],
                "file_name":        att.get("FileName") or att.get("filename"),
                "content_type":     att.get("ContentType") or att.get("contentType"),
                "created_on":       as_utc(att.get("CreatedOn") or att.get("createdAt")),
                "etl_synced_on":    now_iso(),
            })

    upsert(conn, "certifications", certs, conflict_col="id")

    if attachments:
        # Filter out rows missing id
        attachments = [a for a in attachments if a.get("id")]
        upsert(conn, "certification_attachments", attachments, conflict_col="id")

    set_last_sync(conn, "certifications", len(certs))
    log.info(f"  certifications: {len(certs)} rows, {len(attachments)} attachments")


def run(conn):
    sync_certifications(conn)
