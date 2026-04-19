"""
Service Report API routes.

File abuse reports against services and manage report lifecycle.
Auto-delists services with 3+ non-dismissed reports.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.deps import extract_owner, require_admin

router = APIRouter(prefix="/reports", tags=["reports"])


# --- Request models ---

class FileReportRequest(BaseModel):
    service_id: str
    reason: str  # malicious | inaccurate | unavailable | other
    details: str = ""


# --- Routes ---

@router.post("", status_code=201)
async def file_report(req: FileReportRequest, request: Request):
    """File an abuse report against a service.

    Valid reasons: malicious, inaccurate, unavailable, other.
    Services with 3+ non-dismissed reports are automatically delisted.
    """
    from marketplace.report import ReportError

    owner_id, _ = extract_owner(request)
    mgr = _get_manager(request)

    try:
        report = mgr.file_report(
            service_id=req.service_id,
            reporter_id=owner_id,
            reason=req.reason,
            details=req.details,
        )
        return _report_response(report)
    except ReportError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/service/{service_id}")
async def get_reports_for_service(service_id: str, request: Request):
    """List all reports for a service. Admin only."""
    require_admin(request)
    mgr = _get_manager(request)

    reports = mgr.get_reports(service_id)
    return {
        "service_id": service_id,
        "reports": [_report_response(r) for r in reports],
        "count": len(reports),
    }


@router.post("/{report_id}/dismiss")
async def dismiss_report(report_id: str, request: Request):
    """Dismiss an invalid report. Admin only."""
    require_admin(request)
    mgr = _get_manager(request)

    dismissed = mgr.dismiss_report(report_id)
    if not dismissed:
        raise HTTPException(
            status_code=404,
            detail="Report not found or already dismissed",
        )
    return {"status": "dismissed", "report_id": report_id}


# --- Helpers ---

def _get_manager(request: Request):
    return request.app.state.report_mgr


def _report_response(report: dict) -> dict:
    """Serialize report record for API response."""
    return {
        "id": report.get("id", ""),
        "service_id": report.get("service_id", ""),
        "provider_id": report.get("provider_id", ""),
        "reporter_id": report.get("reporter_id", ""),
        "reason": report.get("reason", ""),
        "details": report.get("details", ""),
        "status": report.get("status", ""),
        "created_at": report.get("created_at", ""),
    }
