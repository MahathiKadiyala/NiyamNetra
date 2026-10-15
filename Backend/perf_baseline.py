"""perf_baseline.py — Lightweight performance and memory instrumentation for NiyamNetra image pipeline.

Measures baseline timings and process memory across image upload, quality analysis,
and statutory assessment without modifying API contracts or business logic.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger("niyamnetra.image.bench")


def get_process_memory_mb() -> float:
    """Return process resident / working set memory in megabytes (cross-platform)."""
    # 1. Windows via psapi
    try:
        import ctypes
        import ctypes.wintypes

        class PMC(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.wintypes.DWORD),
                ("PageFaultCount", ctypes.wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        f = ctypes.windll.psapi.GetProcessMemoryInfo
        f.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(PMC), ctypes.wintypes.DWORD]
        f.restype = ctypes.wintypes.BOOL
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        if f(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return round(pmc.WorkingSetSize / (1024 * 1024), 2)
    except Exception:
        pass

    # 2. Linux / macOS via resource
    try:
        import resource
        ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # On Linux ru_maxrss is in kilobytes; on macOS in bytes
        if sys.platform == "darwin":
            return round(ru / (1024 * 1024), 2)
        return round(ru / 1024.0, 2)
    except Exception:
        pass

    return 0.0


# Storage for in-memory baseline metrics
_LATEST_UPLOAD_BENCHMARKS: list[dict[str, Any]] = []
_LATEST_ASSESS_BENCHMARKS: list[dict[str, Any]] = []
_CONTEXT_TIMINGS: dict[int, dict[str, Any]] = {}


def record_context_timing(scan_id: int, timings: dict[str, Any]) -> None:
    """Record internal build_context timings without modifying slotted CheckContext."""
    _CONTEXT_TIMINGS[scan_id] = timings


def pop_context_timing(scan_id: int) -> dict[str, Any]:
    """Retrieve and clear internal build_context timings."""
    return _CONTEXT_TIMINGS.pop(scan_id, {})


def record_upload_timing(timings: dict[str, Any]) -> None:
    """Record a measured upload timing sample."""
    _LATEST_UPLOAD_BENCHMARKS.append(timings)
    if len(_LATEST_UPLOAD_BENCHMARKS) > 50:
        _LATEST_UPLOAD_BENCHMARKS.pop(0)
    logger.info(
        "[IMAGE_BENCH] upload scan=%s panel=%s total=%.2fms read=%.2fms mime=%.2fms "
        "decode=%.2fms quality=%.2fms phash=%.2fms write=%.2fms mem_peak=%.2fMB",
        timings.get("scan_id"),
        timings.get("panel"),
        timings.get("total_ms", 0.0),
        timings.get("byte_read_ms", 0.0),
        timings.get("mime_sniff_ms", 0.0),
        timings.get("decode_ms", 0.0),
        timings.get("quality_ms", 0.0),
        timings.get("phash_ms", 0.0),
        timings.get("file_write_ms", 0.0),
        timings.get("mem_peak_mb", 0.0),
    )


def record_assess_timing(timings: dict[str, Any]) -> None:
    """Record a measured assessment timing sample."""
    _LATEST_ASSESS_BENCHMARKS.append(timings)
    if len(_LATEST_ASSESS_BENCHMARKS) > 50:
        _LATEST_ASSESS_BENCHMARKS.pop(0)
    logger.info(
        "[ASSESS_BENCH] scan=%s total=%.2fms load_disk=%.2fms quad=%.2fms "
        "rectify=%.2fms ocr=%.2fms llm=%.2fms rules=%.2fms db=%.2fms",
        timings.get("scan_id"),
        timings.get("total_ms", 0.0),
        timings.get("load_disk_ms", 0.0),
        timings.get("quad_ms", 0.0),
        timings.get("rectify_ms", 0.0),
        timings.get("ocr_ms", 0.0),
        timings.get("llm_ms", 0.0),
        timings.get("rules_ms", 0.0),
        timings.get("db_persist_ms", 0.0),
    )


def get_latest_benchmarks() -> dict[str, Any]:
    """Retrieve in-memory benchmark records."""
    return {
        "uploads": list(_LATEST_UPLOAD_BENCHMARKS),
        "assessments": list(_LATEST_ASSESS_BENCHMARKS),
    }

