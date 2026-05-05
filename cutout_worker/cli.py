import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy import units as u
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales


CUTOUT_PROGRESS_WEIGHT = 92.0
SUPPORTED_TOOLS = {"astropy", "cutout-fits"}


def utc_now_dt() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def utc_now() -> str:
    return utc_now_dt().isoformat().replace("+00:00", "Z")


def parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def read_json(path: str, default: Any) -> Any:
    if not os.path.isfile(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def write_json_atomic(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    os.replace(temp_path, path)


def append_log(log_path: str, message: str) -> None:
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(f"[{utc_now()}] {message.rstrip()}\n")


def progress_snapshot(job: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "job_id": job.get("job_id"),
        "operation": job.get("operation"),
        "status": job.get("status"),
        "phase": job.get("phase"),
        "job_name": job.get("job_name"),
        "message": job.get("message"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "elapsed_seconds": job.get("elapsed_seconds"),
        "progress_pct": job.get("progress_pct"),
        "processed_items": job.get("processed_items"),
        "total_items": job.get("total_items"),
        "completed_bytes": job.get("completed_bytes"),
        "total_estimated_bytes": job.get("total_estimated_bytes"),
        "current_item": job.get("current_item"),
        "current_output_path": job.get("current_output_path"),
        "layer_ids": job.get("layer_ids") or [],
        "output_paths": job.get("output_paths") or {},
        "cutout_tool": job.get("cutout_tool"),
        "runner": job.get("runner"),
        "session_id": job.get("session_id"),
        "runtime_image": job.get("runtime_image"),
        "heartbeat_at": job.get("heartbeat_at"),
        "updated_at": job.get("updated_at"),
    }


def elapsed_seconds(job: Dict[str, Any]) -> int | None:
    started = parse_utc(job.get("started_at"))
    if not started:
        return None
    finished = parse_utc(job.get("finished_at")) or utc_now_dt()
    return int(max(0.0, round((finished - started).total_seconds())))


def normalize_job(job: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(job)
    normalized.setdefault("status", "queued")
    normalized.setdefault("phase", normalized.get("status") or "queued")
    normalized.setdefault("message", "")
    normalized.setdefault("layer_ids", [])
    normalized.setdefault("output_paths", {})
    normalized.setdefault("progress_pct", 0.0)
    normalized.setdefault("processed_items", 0)
    normalized.setdefault("total_items", 0)
    normalized.setdefault("completed_bytes", 0)
    normalized.setdefault("total_estimated_bytes", 0)
    normalized["elapsed_seconds"] = elapsed_seconds(normalized)
    return normalized


def write_job(job_path: str, progress_path: str, job: Dict[str, Any]) -> Dict[str, Any]:
    payload = normalize_job(job)
    payload["updated_at"] = utc_now()
    payload["heartbeat_at"] = payload["updated_at"]
    write_json_atomic(job_path, payload)
    write_json_atomic(progress_path, progress_snapshot(payload))
    return payload


def collapse_cube_cutout_to_image(path: str) -> None:
    temp_path = f"{path}.image2d.tmp"
    with fits.open(path, memmap=False) as hdul:
        rewritten: List[fits.hdu.base.ExtensionHDU] = []
        collapsed = False
        for hdu in hdul:
            if collapsed or not isinstance(hdu, (fits.PrimaryHDU, fits.ImageHDU)) or hdu.data is None:
                rewritten.append(hdu.copy())
                continue
            data = np.asarray(hdu.data)
            if data.ndim < 3:
                rewritten.append(hdu.copy())
                continue
            header = hdu.header.copy()
            plane_index = int(data.shape[0] // 2)
            collapsed_data = np.asarray(data[plane_index])
            header["NAXIS"] = 2
            header["NAXIS1"] = int(collapsed_data.shape[-1])
            header["NAXIS2"] = int(collapsed_data.shape[-2])
            max_axis = int(header.get("WCSAXES") or max(2, int(hdu.header.get("NAXIS") or data.ndim)))
            header["WCSAXES"] = 2
            for axis in range(3, max_axis + 1):
                for prefix in ("NAXIS", "CRPIX", "CRVAL", "CDELT", "CUNIT", "CTYPE", "CROTA", "CNAME"):
                    header.pop(f"{prefix}{axis}", None)
                for other in range(1, max_axis + 1):
                    header.pop(f"PC{axis}_{other}", None)
                    header.pop(f"PC{other}_{axis}", None)
                    header.pop(f"CD{axis}_{other}", None)
                    header.pop(f"CD{other}_{axis}", None)
            rewritten.append(type(hdu)(data=collapsed_data, header=header))
            collapsed = True
        fits.HDUList(rewritten).writeto(temp_path, overwrite=True)
    os.replace(temp_path, path)


def run_cutout_astropy(item: Dict[str, Any], log_handle: Any) -> None:
    source_path = str(item["source_path"])
    target_path = str(item["target_path"])
    ra_deg = float(item["ra_deg"])
    dec_deg = float(item["dec_deg"])
    radius_deg = max(float(item["radius_deg"]), 0.0001)
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    log_handle.write(f"$ astropy-cutout {source_path} -> {target_path} @ ({ra_deg}, {dec_deg}) r={radius_deg}deg\n")
    log_handle.flush()
    sky = SkyCoord(ra_deg * u.deg, dec_deg * u.deg, frame="icrs")
    with fits.open(source_path, memmap=True) as hdul:
        rewritten: List[fits.hdu.base.ExtensionHDU] = []
        cutout_written = False
        for hdu in hdul:
            if cutout_written or not isinstance(hdu, (fits.PrimaryHDU, fits.ImageHDU)) or hdu.data is None:
                rewritten.append(hdu.copy())
                continue
            data = np.asarray(hdu.data)
            if data.ndim < 2:
                rewritten.append(hdu.copy())
                continue
            header = hdu.header.copy()
            celestial = WCS(header).celestial
            center_x, center_y = celestial.world_to_pixel(sky)
            scales = proj_plane_pixel_scales(celestial)
            scale_x = abs(float(scales[0])) if len(scales) > 0 else 0.0
            scale_y = abs(float(scales[1])) if len(scales) > 1 else scale_x
            if scale_x <= 0 or scale_y <= 0:
                raise RuntimeError(f"Could not determine a valid celestial pixel scale for {source_path}")
            half_w = max(1, int(math.ceil(radius_deg / scale_x)))
            half_h = max(1, int(math.ceil(radius_deg / scale_y)))
            x_center = int(round(center_x))
            y_center = int(round(center_y))
            x1 = max(0, x_center - half_w)
            x2 = min(int(data.shape[-1]), x_center + half_w + 1)
            y1 = max(0, y_center - half_h)
            y2 = min(int(data.shape[-2]), y_center + half_h + 1)
            slicer = [slice(None)] * data.ndim
            slicer[-1] = slice(x1, x2)
            slicer[-2] = slice(y1, y2)
            cutout = np.asarray(data[tuple(slicer)])
            header["NAXIS1"] = int(cutout.shape[-1])
            header["NAXIS2"] = int(cutout.shape[-2])
            if "CRPIX1" in header:
                header["CRPIX1"] = float(header["CRPIX1"]) - x1
            if "CRPIX2" in header:
                header["CRPIX2"] = float(header["CRPIX2"]) - y1
            rewritten.append(type(hdu)(data=cutout, header=header))
            cutout_written = True
        fits.HDUList(rewritten).writeto(target_path, overwrite=True)


def run_cutout_fits(item: Dict[str, Any], log_handle: Any) -> subprocess.Popen:
    source_path = str(item["source_path"])
    target_path = str(item["target_path"])
    ra_deg = float(item["ra_deg"])
    dec_deg = float(item["dec_deg"])
    radius_arcmin = max(float(item["radius_deg"]) * 60.0, 0.01)
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    cutout_cli = shutil.which("cutout-fits")
    command = (
        [cutout_cli, source_path, target_path, str(ra_deg), str(dec_deg), str(radius_arcmin), "-o"]
        if cutout_cli
        else [sys.executable, "-m", "cutout_fits.cutout", source_path, target_path, str(ra_deg), str(dec_deg), str(radius_arcmin), "-o"]
    )
    log_handle.write(f"$ {' '.join(command)}\n")
    log_handle.flush()
    return subprocess.Popen(
        command,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )


def run_manifest(manifest_path: str) -> int:
    manifest = read_json(manifest_path, None)
    if not isinstance(manifest, dict):
        raise RuntimeError(f"Invalid manifest: {manifest_path}")
    tool = str(manifest.get("cutout_tool") or "").strip()
    if tool not in SUPPORTED_TOOLS:
        raise RuntimeError(f"Unsupported cutout tool: {tool or '<missing>'}")

    job_paths = manifest.get("job_paths") or {}
    job_path = str(job_paths.get("job_json") or "")
    progress_path = str(job_paths.get("progress_json") or "")
    log_path = str(job_paths.get("log_path") or "")
    if not job_path or not progress_path or not log_path:
        raise RuntimeError("Manifest is missing required job_paths")

    job = read_json(job_path, {})
    if not isinstance(job, dict):
        job = {}
    job.update(
        {
            "job_id": manifest.get("job_id") or job.get("job_id"),
            "job_name": manifest.get("job_name") or job.get("job_name"),
            "operation": manifest.get("operation") or job.get("operation"),
            "cutout_tool": tool,
            "runner": "headless-session",
            "message": "Worker starting",
            "status": "running",
            "phase": "starting",
        }
    )
    if not job.get("started_at"):
        job["started_at"] = utc_now()
    work_items = list(manifest.get("work_items") or [])
    total_items = len(work_items)
    total_estimated_bytes = max(1, int(sum(max(1, int(item.get("estimate_bytes") or 1)) for item in work_items)))
    job["total_items"] = total_items
    job["total_estimated_bytes"] = total_estimated_bytes
    job["output_paths"] = {**dict(job.get("output_paths") or {}), **job_paths}
    job = write_job(job_path, progress_path, job)
    append_log(log_path, f"Worker started with tool {tool}")

    processed_bytes = int(job.get("completed_bytes") or 0)
    layer_mode = str(manifest.get("layer_mode") or "image2d").strip().lower()
    with open(log_path, "a", encoding="utf-8") as log_handle:
        for index, item in enumerate(work_items, start=1):
            basename = os.path.basename(str(item.get("source_path") or f"item-{index}"))
            remaining_estimate = sum(max(1, int(next_item.get("estimate_bytes") or 1)) for next_item in work_items[index:])
            job.update(
                {
                    "status": "running",
                    "phase": "cutting",
                    "message": f"Generating cutout {index} of {total_items}",
                    "processed_items": index - 1,
                    "current_item": basename,
                    "current_output_path": str(item.get("target_path") or ""),
                }
            )
            job = write_job(job_path, progress_path, job)
            append_log(log_path, f"Cutting {basename}")
            target_path = str(item.get("target_path") or "")
            estimate_bytes = max(1, int(item.get("estimate_bytes") or 1))
            if tool == "astropy":
                run_cutout_astropy(item, log_handle)
                current_size = os.path.getsize(target_path) if os.path.isfile(target_path) else estimate_bytes
                visible_total = max(total_estimated_bytes, processed_bytes + remaining_estimate + max(current_size, estimate_bytes))
                completed = processed_bytes + min(current_size, estimate_bytes)
                job.update(
                    {
                        "status": "running",
                        "phase": "cutting",
                        "progress_pct": min(CUTOUT_PROGRESS_WEIGHT, CUTOUT_PROGRESS_WEIGHT * (completed / max(1, visible_total))),
                        "completed_bytes": completed,
                        "total_estimated_bytes": visible_total,
                        "processed_items": index - 1,
                        "current_item": basename,
                        "current_output_path": target_path,
                    }
                )
                job = write_job(job_path, progress_path, job)
            else:
                process = run_cutout_fits(item, log_handle)
                while True:
                    return_code = process.poll()
                    current_size = os.path.getsize(target_path) if os.path.isfile(target_path) else 0
                    visible_total = max(total_estimated_bytes, processed_bytes + remaining_estimate + max(current_size, estimate_bytes))
                    completed = processed_bytes + min(current_size, estimate_bytes)
                    job.update(
                        {
                            "status": "running",
                            "phase": "cutting",
                            "progress_pct": min(CUTOUT_PROGRESS_WEIGHT, CUTOUT_PROGRESS_WEIGHT * (completed / max(1, visible_total))),
                            "completed_bytes": completed,
                            "total_estimated_bytes": visible_total,
                            "processed_items": index - 1,
                            "current_item": basename,
                            "current_output_path": target_path,
                        }
                    )
                    job = write_job(job_path, progress_path, job)
                    if return_code is not None:
                        if return_code != 0:
                            raise RuntimeError(f"cutout-fits failed for {basename}")
                        break
                    time.sleep(0.5)
            if layer_mode == "image2d":
                collapse_cube_cutout_to_image(target_path)
            actual_size = os.path.getsize(target_path) if os.path.isfile(target_path) else 0
            processed_bytes += max(actual_size, estimate_bytes)
            total_estimated_bytes = max(total_estimated_bytes, processed_bytes + remaining_estimate)
            job.update(
                {
                    "status": "running",
                    "phase": "cutting",
                    "message": f"Generated {index} of {total_items} cutouts",
                    "processed_items": index,
                    "progress_pct": min(CUTOUT_PROGRESS_WEIGHT, CUTOUT_PROGRESS_WEIGHT * (processed_bytes / max(1, total_estimated_bytes))),
                    "completed_bytes": processed_bytes,
                    "total_estimated_bytes": total_estimated_bytes,
                }
            )
            job = write_job(job_path, progress_path, job)

    job.update(
        {
            "status": "running",
            "phase": "cutout_completed",
            "message": "Cutouts ready for HiPS build",
            "processed_items": total_items,
            "progress_pct": CUTOUT_PROGRESS_WEIGHT,
            "current_item": "",
            "current_output_path": "",
            "worker_finished_at": utc_now(),
        }
    )
    write_job(job_path, progress_path, job)
    append_log(log_path, "Cutout worker completed successfully")
    return 0


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a manifest-driven CANFAR cutout job")
    subparsers = parser.add_subparsers(dest="command", required=False)
    run_parser = subparsers.add_parser("run", help="Run a cutout job manifest")
    run_parser.add_argument("--manifest", required=True, help="Path to manifest.json")
    args = parser.parse_args(argv)
    if args.command != "run":
        parser.print_help()
        return 0
    try:
        return run_manifest(os.path.abspath(args.manifest))
    except Exception as exc:
        manifest = read_json(os.path.abspath(args.manifest), {})
        job_paths = manifest.get("job_paths") or {}
        job_path = str(job_paths.get("job_json") or "")
        progress_path = str(job_paths.get("progress_json") or "")
        log_path = str(job_paths.get("log_path") or "")
        if log_path:
            append_log(log_path, f"ERROR: {exc}")
        if job_path and progress_path:
            job = read_json(job_path, {})
            if not isinstance(job, dict):
                job = {}
            job.update(
                {
                    "status": "error",
                    "phase": "error",
                    "message": str(exc),
                    "finished_at": utc_now(),
                    "runner": "headless-session",
                    "cutout_tool": str(manifest.get("cutout_tool") or job.get("cutout_tool") or ""),
                }
            )
            write_job(job_path, progress_path, job)
        raise
