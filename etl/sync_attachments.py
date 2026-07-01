"""
etl/sync_attachments.py
Syncs attachment METADATA only — no binary content is fetched or stored.
Binary files are proxied on demand via the Azure Function.

Fetches attachments for each domain object type by iterating
over known entity IDs already in the database.
"""

import logging
from .utils import api_get, upsert, set_last_sync, now_iso

log = logging.getLogger(__name__)

# kind int → (api_path_template, id_column, table)
ENTITY_MAP = {
    0: ("companies",         "company_id",    "/attachments/domainObject/{id}"),
    1: ("equipments",        "equipment_id",  "/attachments/domainObject/{id}"),
    3: ("workers",           "id",            "/attachments/domainObject/{id}"),
    4: ("locations",         "id",            "/attachments/domainObject/{id}"),
    5: ("form_types",        "id",            "/attachments/domainObject/{id}"),
    2: ("incident_folders",  "id",            "/attachments/domainObject/{id}"),
}


def _fetch_entity_ids(conn, table: str, id_col: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {id_col}::text FROM {table}")
        return [row[0] for row in cur.fetchall()]


def _map_attachment(r: dict) -> dict | None:
    aid = r.get("attachmentId")
    if not aid:
        return None
    return {
        "attachment_id":      aid,
        "domain_object_id":   r.get("domainObjectId"),
        "kind":               r.get("kind"),
        "original_file_name": r.get("originalFileName"),
        "file_size_in_kb":    r.get("fileSizeInKb"),
        "content_type":       r.get("contentType"),
        "name":               r.get("name"),
        "company_id":         r.get("companyId"),
        "general_type_id":    r.get("generalTypeId"),
        "effective_on":       r.get("effectiveOn") if r.get("effectiveOn") not in (None, {}) else None,
        "expires_on":         r.get("expiresOn") if r.get("expiresOn") not in (None, {}) else None,
        "created_by":         r.get("createdBy"),
        "updated_by":         r.get("updatedBy"),
        "created_at":         r.get("createdAt"),
        "updated_at":         r.get("updatedAt"),
        "etl_synced_on":      now_iso(),
    }


def sync_attachments(conn):
    log.info("Syncing attachments (metadata only)...")
    total = 0
    errors = 0

    for kind, (table, id_col, path_template) in ENTITY_MAP.items():
        entity_ids = _fetch_entity_ids(conn, table, id_col)
        log.info(f"  kind={kind} ({table}): {len(entity_ids)} entities to check")

        batch = []
        for eid in entity_ids:
            try:
                result = api_get(f"/attachments/domainObject/{eid}")
                if not result:
                    continue

                # Response is an array of AttachmentViewModel
                if isinstance(result, dict):
                    result = [result]

                for r in result:
                    row = _map_attachment(r)
                    if row:
                        batch.append(row)

                # Upsert in chunks to avoid memory buildup
                if len(batch) >= 200:
                    upsert(conn, "attachments", batch, conflict_col="attachment_id")
                    total += len(batch)
                    batch = []

            except Exception as e:
                log.debug(f"    attachment fetch failed for {eid}: {e}")
                errors += 1

        if batch:
            upsert(conn, "attachments", batch, conflict_col="attachment_id")
            total += len(batch)
            batch = []

    set_last_sync(conn, "attachments", total)
    log.info(f"  attachments: {total} metadata rows ({errors} skipped)")


def run(conn):
    sync_attachments(conn)
