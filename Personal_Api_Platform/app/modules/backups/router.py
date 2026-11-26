"""FastAPI router for Homelab Unified Backup & Disaster Recovery (Non-Blocking & Async-Capable)."""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.backups.service import BackupService

router = APIRouter(prefix="/backups", tags=["Backups & Disaster Recovery"])


class CreateBackupRequest(BaseModel):
    scope: str = Field(default="all", description="Scope: 'all', 'databases', 'configs', or 'psm'")
    note: Optional[str] = Field(default="", description="Optional label or note for this backup")
    target_databases: Optional[List[str]] = Field(default=None, description="Optional list of specific database names to dump")
    sync_to_s3: bool = Field(default=True, description="Replicate archive to Homelab MinIO S3 'backups' bucket")
    async_mode: bool = Field(default=False, description="If true, enqueues non-blocking background job and returns 202 immediately")


class PruneBackupsRequest(BaseModel):
    keep_days: int = Field(default=7, ge=1, le=365)
    keep_min: int = Field(default=3, ge=1, le=50)


@router.get("/flow-status")
async def get_backup_flow_status():
    """Returns end-to-end Homelab Backup Pipeline status, local vault, MinIO S3 vault, and active jobs."""
    data = await run_in_threadpool(BackupService.get_flow_status)
    return {"success": True, "data": data}


@router.get("/jobs")
async def list_backup_jobs():
    """Lists queued, running, and recently finished asynchronous backup jobs."""
    jobs = BackupService.list_backup_jobs()
    return {"success": True, "count": len(jobs), "data": jobs}


@router.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
async def enqueue_async_backup_job(payload: Optional[CreateBackupRequest] = None):
    """Enqueues a non-blocking background backup job on PAP's worker pool (<1ms response)."""
    req = payload or CreateBackupRequest()
    job = BackupService.enqueue_backup_job(
        scope=req.scope,
        note=req.note or "",
        target_databases=req.target_databases,
        sync_to_s3=req.sync_to_s3,
    )
    return {
        "success": True,
        "message": f"Backup job '{job['job_id']}' queued in background",
        "data": job,
    }


@router.get("/jobs/{job_id}")
async def get_backup_job_status(job_id: str):
    """Returns live progress stage and completion metadata for an async backup job."""
    job = BackupService.get_backup_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backup job not found")
    return {"success": True, "data": job}


@router.post("/create", status_code=status.HTTP_201_CREATED)
async def trigger_backup(
    response: Response,
    payload: Optional[CreateBackupRequest] = None,
    db: Session = Depends(get_db),
):
    """Triggers a Homelab backup without blocking PAP Core's async event loop."""
    req = payload or CreateBackupRequest()
    if req.async_mode:
        response.status_code = status.HTTP_202_ACCEPTED
        job = BackupService.enqueue_backup_job(
            scope=req.scope,
            note=req.note or "",
            target_databases=req.target_databases,
            sync_to_s3=req.sync_to_s3,
        )
        return {
            "success": True,
            "message": f"Backup job '{job['job_id']}' dispatched asynchronously",
            "data": job,
        }

    try:
        manifest = await run_in_threadpool(
            BackupService.create_backup,
            req.scope,
            req.note or "",
            req.target_databases,
            req.sync_to_s3,
            db,
        )
        s3_note = " + S3 synced" if manifest.get("s3_replication", {}).get("synced") else ""
        return {
            "success": True,
            "message": f"Backup created successfully ({manifest['archive']['size_human']}{s3_note})",
            "data": manifest,
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create backup: {e}",
        )


@router.post("/prune")
async def prune_old_backups(payload: Optional[PruneBackupsRequest] = None):
    """Enforces Homelab backup retention policy while protecting minimum latest archives."""
    req = payload or PruneBackupsRequest()
    report = await run_in_threadpool(BackupService.prune_backups, req.keep_days, req.keep_min)
    return {
        "success": True,
        "message": f"Pruned {report['pruned']} old backup(s); {report['remaining']} retained.",
        "data": report,
    }


@router.get("")
async def list_backups():
    """Lists all available Homelab backup archives with sizes, S3 replication status, checksums, and timestamps."""
    backups = await run_in_threadpool(BackupService.list_backups)
    total_bytes = sum(b.get("size_bytes", 0) for b in backups)
    return {
        "success": True,
        "count": len(backups),
        "total_storage_bytes": total_bytes,
        "data": backups,
    }


@router.get("/{filename}/download")
async def download_backup(filename: str):
    """Downloads the selected .tar.gz backup archive."""
    path = await run_in_threadpool(BackupService.get_backup_path, filename)
    if not path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backup archive not found")

    return FileResponse(
        path=path,
        media_type="application/gzip",
        filename=path.name,
    )


@router.get("/{filename}/manifest")
async def view_backup_manifest(filename: str):
    """Returns the JSON manifest detailing files, database row counts, S3 status, and checksums."""
    manifest = await run_in_threadpool(BackupService.get_backup_manifest, filename)
    if not manifest:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backup or manifest not found")
    return {"success": True, "data": manifest}


@router.post("/{filename}/verify")
@router.get("/{filename}/verify")
async def verify_backup_integrity(filename: str):
    """Verifies the integrity of a backup archive off the event loop."""
    report = await run_in_threadpool(BackupService.verify_backup, filename)
    if not report.get("exists"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backup archive not found")
    return {
        "success": True,
        "message": f"Verification completed: {report.get('status')} ({report.get('integrity_score_percent', 0)}%)",
        "data": report,
    }


@router.delete("/{filename}")
async def delete_backup(filename: str):
    """Permanently deletes a backup archive and its sidecar metadata."""
    success = await run_in_threadpool(BackupService.delete_backup, filename)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backup archive not found or deletion failed")
    return {"success": True, "message": f"Backup {filename} deleted successfully"}
