"""
routes/proxies.py
Blueprints for SiteDocs proxy endpoints: PDF, signatures, viewer URL,
attachment download.
"""

import os
import logging
import azure.functions as func
from shared.db import query, require_api_key, ok, error, not_found, _get_secret

bp = func.Blueprint()
log = logging.getLogger(__name__)


@bp.route(route="forms/{formId}/pdf", methods=["GET"])
@require_api_key
def get_form_pdf(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/forms/{formId}/pdf
    Proxies the SiteDocs PDF export back to the caller.
    Query param: companyId (optional - defaults to SITEDOCS_COMPANY_ID env var)
    """
    import requests as req_lib

    form_id = req.route_params.get("formId")
    company_id = req.params.get("companyId") or os.environ.get("SITEDOCS_COMPANY_ID", "")

    if not company_id:
        return error(400, "companyId required — pass as query param or set SITEDOCS_COMPANY_ID env var")

    sitedocs_token = _get_secret("SITEDOCS_API_TOKEN")
    sitedocs_url = f"https://api-1.sitedocs.com/api/v1/export/pdf/company/{company_id}/form/{form_id}"

    try:
        upstream = req_lib.get(
            sitedocs_url,
            headers={"Authorization": sitedocs_token},
            timeout=30,
        )
        if upstream.status_code == 404:
            return not_found("Form PDF")
        if upstream.status_code == 401:
            return error(502, "SiteDocs authorization failed")
        upstream.raise_for_status()

        return func.HttpResponse(
            body=upstream.content,
            status_code=200,
            mimetype="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="form-{form_id}.pdf"',
                "Content-Length": str(len(upstream.content)),
                "Cache-Control": "private, max-age=300",
            }
        )

    except req_lib.exceptions.Timeout:
        return error(504, "SiteDocs PDF request timed out")
    except req_lib.exceptions.RequestException as e:
        log.error(f"PDF proxy error for {form_id}: {e}")
        return error(502, "Failed to retrieve PDF from SiteDocs")


@bp.route(route="forms/{formId}/signatures", methods=["GET"])
@require_api_key
def get_form_signatures(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/forms/{formId}/signatures
    Thin proxy over SiteDocs GET /api/v1/signatures?formId={formId}.
    """
    import requests as req_lib

    form_id = req.route_params.get("formId")
    if not form_id:
        return error(400, "formId required")

    sitedocs_token = _get_secret("SITEDOCS_API_TOKEN")
    sitedocs_url = "https://api-1.sitedocs.com/api/v1/signatures"

    try:
        upstream = req_lib.get(
            sitedocs_url,
            params={"formId": form_id},
            headers={"Authorization": sitedocs_token, "Accept": "application/json"},
            timeout=20,
        )
        if upstream.status_code == 401:
            return error(502, "SiteDocs authorization failed")
        upstream.raise_for_status()
        return func.HttpResponse(
            body=upstream.content,
            status_code=200,
            mimetype="application/json",
            headers={"Cache-Control": "private, max-age=60"},
        )
    except req_lib.exceptions.Timeout:
        return error(504, "SiteDocs signatures request timed out")
    except req_lib.exceptions.RequestException as e:
        log.error(f"Signatures proxy error for {form_id}: {e}")
        return error(502, "Failed to retrieve signatures from SiteDocs")


@bp.route(route="forms/{formId}/viewer-url", methods=["GET"])
@require_api_key
def get_form_viewer_url(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/forms/{formId}/viewer-url
    Returns the public SiteDocs web viewer URL + PDF URL for a form.
    """
    form_id = req.route_params.get("formId")
    company_id = req.params.get("companyId") or os.environ.get("SITEDOCS_COMPANY_ID", "")

    if not company_id:
        return error(400, "companyId required")

    return ok({
        "formId":    form_id,
        "viewerUrl": f"https://web.sitedocs.com/company/{company_id}/form/{form_id}",
        "pdfUrl":    f"/api/forms/{form_id}/pdf?companyId={company_id}",
    })


@bp.route(route="attachments/{attachmentId}/download", methods=["GET"])
@require_api_key
def download_attachment(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/attachments/{attachmentId}/download
    Looks up attachment metadata in PostgreSQL, then proxies
    the binary content from SiteDocs.
    """
    import requests as req_lib

    attachment_id = req.route_params.get("attachmentId")

    rows = query("""
        SELECT attachment_id, domain_object_id, kind,
               original_file_name, content_type, company_id
        FROM attachments
        WHERE attachment_id = %s
    """, (attachment_id,))

    if not rows:
        return not_found("Attachment")

    meta = rows[0]
    domain_object_id = meta["domain_object_id"]
    file_name = meta["original_file_name"] or attachment_id
    content_type = meta["content_type"] or "application/octet-stream"

    kind_map = {
        0: "Contractor", 1: "Equipment", 2: "Incident",
        3: "Worker", 4: "Project", 5: "FormType",
        6: "Resource", 7: "ProcessRun"
    }
    kind_str = kind_map.get(meta["kind"], "Project")

    sitedocs_token = _get_secret("SITEDOCS_API_TOKEN")
    sitedocs_url = "https://api-1.sitedocs.com/api/v1/attachments/content"

    try:
        upstream = req_lib.get(
            sitedocs_url,
            headers={"Authorization": sitedocs_token},
            params={
                "attachmentId":   attachment_id,
                "domainObjectId": domain_object_id,
                "kind":           kind_str,
                "fileName":       file_name,
            },
            timeout=30,
        )

        if upstream.status_code == 404:
            return not_found("Attachment content")
        if upstream.status_code == 401:
            return error(502, "SiteDocs authorization failed")
        upstream.raise_for_status()

        return func.HttpResponse(
            body=upstream.content,
            status_code=200,
            mimetype=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{file_name}"',
                "Content-Length": str(len(upstream.content)),
                "Cache-Control": "private, max-age=300",
            }
        )

    except req_lib.exceptions.Timeout:
        return error(504, "SiteDocs request timed out")
    except req_lib.exceptions.RequestException as e:
        log.error(f"SiteDocs proxy error for {attachment_id}: {e}")
        return error(502, "Failed to retrieve attachment from SiteDocs")
