#!/usr/bin/env python3
"""
MCP Server for yt-dlp with WireGuard VPN management.
Detects video region and selects appropriate WireGuard config from any country.
Includes download queue for sequential processing on resource-constrained devices.
"""
import asyncio
import json
import re
import subprocess
from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
import threading

from fastmcp import FastMCP

# Initialize MCP server
mcp = FastMCP("yt-dlp-vpn")

# Configuration
WIREGUARD_DIR = Path("/etc/wireguard")
DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads"


# Queue management
class DownloadStatus(Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class DownloadJob:
    id: int
    url: str
    status: DownloadStatus
    auto_vpn: bool
    preferred_city: Optional[str]
    output_dir: Optional[str]
    format_spec: str
    added_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None
    result: Optional[str] = None


class DownloadQueue:
    def __init__(self):
        self.queue: deque[DownloadJob] = deque()
        self.history: list[DownloadJob] = []
        self.current_job: Optional[DownloadJob] = None
        self.next_id = 1
        self.lock = threading.Lock()
        self.worker_thread: Optional[threading.Thread] = None
        self.running = False

    def add(self, url: str, auto_vpn: bool = True, preferred_city: Optional[str] = None,
            output_dir: Optional[str] = None, format_spec: str = "best") -> DownloadJob:
        with self.lock:
            job = DownloadJob(
                id=self.next_id,
                url=url,
                status=DownloadStatus.QUEUED,
                auto_vpn=auto_vpn,
                preferred_city=preferred_city,
                output_dir=output_dir,
                format_spec=format_spec,
                added_at=datetime.now().isoformat()
            )
            self.next_id += 1
            self.queue.append(job)
            self._ensure_worker()
            return job

    def _ensure_worker(self):
        if not self.running:
            self.running = True
            self.worker_thread = threading.Thread(target=self._worker, daemon=True)
            self.worker_thread.start()

    def _worker(self):
        while self.running:
            job = None
            with self.lock:
                if self.queue:
                    job = self.queue.popleft()
                    self.current_job = job

            if job:
                self._process_job(job)
                with self.lock:
                    self.history.append(job)
                    self.current_job = None
            else:
                # No jobs, sleep briefly
                threading.Event().wait(1)

    def _process_job(self, job: DownloadJob):
        job.status = DownloadStatus.DOWNLOADING
        job.started_at = datetime.now().isoformat()

        try:
            result = _download_video_internal(
                job.url,
                job.auto_vpn,
                job.preferred_city,
                job.output_dir,
                job.format_spec
            )
            job.status = DownloadStatus.COMPLETED
            job.result = result
        except Exception as e:
            job.status = DownloadStatus.FAILED
            job.error = str(e)
        finally:
            job.completed_at = datetime.now().isoformat()

    def get_status(self) -> dict:
        with self.lock:
            return {
                "current": asdict(self.current_job) if self.current_job else None,
                "queued": [asdict(j) for j in self.queue],
                "recent_history": [asdict(j) for j in self.history[-10:]]
            }

    def cancel(self, job_id: int) -> bool:
        with self.lock:
            for i, job in enumerate(self.queue):
                if job.id == job_id:
                    job.status = DownloadStatus.FAILED
                    job.error = "Cancelled by user"
                    job.completed_at = datetime.now().isoformat()
                    self.queue.remove(job)
                    self.history.append(job)
                    return True
            return False

    def clear_history(self):
        with self.lock:
            self.history.clear()


# Global queue instance
download_queue = DownloadQueue()


def get_available_configs() -> list[Path]:
    """Get all available WireGuard configs."""
    if not WIREGUARD_DIR.exists():
        return []
    return sorted(WIREGUARD_DIR.glob("*.conf"))


def parse_config_location(config_path: Path) -> dict[str, str]:
    """Parse location info from config filename (e.g., au-syd-wg-001.conf)."""
    match = re.match(r"([a-z]{2})-([a-z]{3})-", config_path.stem)
    if match:
        return {
            "country": match.group(1),
            "city": match.group(2),
            "full": config_path.stem
        }
    return {}


def get_active_wireguard() -> Optional[str]:
    """Check if any WireGuard interface is currently active."""
    try:
        result = subprocess.run(
            ["wg", "show", "interfaces"],
            capture_output=True,
            text=True,
            check=True
        )
        interfaces = result.stdout.strip().split()
        return interfaces[0] if interfaces else None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def wireguard_up(config_path: Path) -> tuple[bool, str]:
    """Bring up a WireGuard interface."""
    interface = config_path.stem
    try:
        subprocess.run(
            ["sudo", "wg-quick", "up", interface],
            capture_output=True,
            text=True,
            check=True,
            timeout=30
        )
        return True, f"WireGuard interface {interface} is now up"
    except subprocess.CalledProcessError as e:
        return False, f"Failed to start {interface}: {e.stderr}"
    except subprocess.TimeoutExpired:
        return False, f"Timeout starting {interface}"


def wireguard_down(interface: str) -> tuple[bool, str]:
    """Bring down a WireGuard interface."""
    try:
        subprocess.run(
            ["sudo", "wg-quick", "down", interface],
            capture_output=True,
            text=True,
            check=True,
            timeout=30
        )
        return True, f"WireGuard interface {interface} is now down"
    except subprocess.CalledProcessError as e:
        return False, f"Failed to stop {interface}: {e.stderr}"
    except subprocess.TimeoutExpired:
        return False, f"Timeout stopping {interface}"


def detect_url_country(url: str) -> Optional[str]:
    """
    Detect the target country from URL using heuristics.
    Returns ISO 3166-1 alpha-2 country code or None.
    """
    url_lower = url.lower()
    parsed = urlparse(url)
    domain = parsed.netloc.lower()

    # UK domains and services
    if any(x in domain for x in ["bbc.co.uk", "iplayer", "itv.com", "channel4.com", "channel5.com"]):
        return "gb"
    if ".uk" in domain or "britain" in url_lower or "british" in url_lower:
        return "gb"

    # US domains and services
    if any(x in domain for x in ["hulu.com", "nbc.com", "abc.com", "cbs.com", "fox.com", "hbo.com", "peacocktv.com"]):
        return "us"
    if ".us" in domain:
        return "us"

    # Canadian domains and services
    if any(x in domain for x in ["cbc.ca", "ctv.ca", "globaltv.com"]):
        return "ca"
    if ".ca" in domain or "canada" in url_lower or "canadian" in url_lower:
        return "ca"

    # Australian domains and services
    if any(x in domain for x in ["abc.net.au", "sbs.com.au", "9now.com.au", "10play.com.au", "7plus.com.au"]):
        return "au"
    if ".au" in domain or "australia" in url_lower:
        return "au"

    # New Zealand
    if ".nz" in domain or "newzealand" in url_lower:
        return "nz"

    # European countries
    if ".de" in domain or "germany" in url_lower or "deutsche" in url_lower:
        return "de"
    if ".fr" in domain or "france" in url_lower:
        return "fr"
    if ".it" in domain or "italy" in url_lower or "italia" in url_lower:
        return "it"
    if ".es" in domain or "spain" in url_lower or "españa" in url_lower:
        return "es"
    if ".nl" in domain or "netherlands" in url_lower:
        return "nl"
    if ".se" in domain or "sweden" in url_lower:
        return "se"
    if ".no" in domain or "norway" in url_lower:
        return "no"
    if ".dk" in domain or "denmark" in url_lower:
        return "dk"

    # Asian countries
    if ".jp" in domain or "japan" in url_lower:
        return "jp"
    if ".kr" in domain or "korea" in url_lower:
        return "kr"
    if ".sg" in domain or "singapore" in url_lower:
        return "sg"
    if ".hk" in domain or "hongkong" in url_lower:
        return "hk"

    # YouTube and other generic platforms - no VPN by default (or use local)
    if "youtube.com" in domain or "youtu.be" in domain:
        return None  # Let it default to no VPN or user's location

    # Default: no VPN (direct connection)
    return None


def get_configs_by_country() -> dict[str, list[Path]]:
    """Group all WireGuard configs by country code."""
    configs = get_available_configs()
    by_country = {}

    for config in configs:
        location = parse_config_location(config)
        country = location.get("country")
        if country:
            if country not in by_country:
                by_country[country] = []
            by_country[country].append(config)

    return by_country


def select_best_config(country: Optional[str] = None, preferred_city: Optional[str] = None) -> Optional[Path]:
    """
    Select the best WireGuard config based on country and optional city preference.
    Returns None if no suitable config found or if country is None (direct connection).
    """
    if country is None:
        return None

    configs = get_available_configs()

    # Filter by country
    country_configs = []
    for config in configs:
        location = parse_config_location(config)
        if location.get("country") == country:
            country_configs.append((config, location))

    if not country_configs:
        return None

    # If preferred city specified, try to match
    if preferred_city:
        for config, location in country_configs:
            if location.get("city") == preferred_city:
                return config

    # Otherwise return first available
    return country_configs[0][0]


@mcp.tool()
def list_wireguard_configs() -> str:
    """List all available WireGuard configurations grouped by country."""
    by_country = get_configs_by_country()

    if not by_country:
        return "No WireGuard configs found in /etc/wireguard"

    active = get_active_wireguard()

    lines = ["Available WireGuard configurations by country:"]
    for country in sorted(by_country.keys()):
        lines.append(f"\n{country.upper()}:")
        for config in by_country[country]:
            location = parse_config_location(config)
            status = " (ACTIVE)" if active and active == config.stem else ""
            lines.append(f"  - {config.stem}{status} [{location.get('city', '?')}]")

    return "\n".join(lines)


@mcp.tool()
def wireguard_status() -> str:
    """Check current WireGuard VPN status."""
    active = get_active_wireguard()
    if active:
        return f"WireGuard interface '{active}' is currently active"
    return "No WireGuard interface is currently active"


@mcp.tool()
def start_wireguard(config_name: Optional[str] = None, country: Optional[str] = None, city: Optional[str] = None) -> str:
    """
    Start a WireGuard VPN connection.

    Args:
        config_name: Specific config name (e.g., 'gb-lon-wg-001'), or None to auto-select
        country: Country code (e.g., 'gb', 'us', 'ca'), or None for any
        city: Preferred city code (e.g., 'lon', 'nyc', 'tor'), or None for any
    """
    # Check if already active
    active = get_active_wireguard()
    if active:
        return f"WireGuard interface '{active}' is already active. Stop it first."

    # Select config
    if config_name:
        config_path = WIREGUARD_DIR / f"{config_name}.conf"
        if not config_path.exists():
            return f"Config '{config_name}' not found"
    else:
        config_path = select_best_config(country=country, preferred_city=city)
        if not config_path:
            msg = "No suitable WireGuard config found"
            if country:
                msg += f" for country '{country}'"
            return msg

    # Start VPN
    success, message = wireguard_up(config_path)
    return message


@mcp.tool()
def stop_wireguard() -> str:
    """Stop the currently active WireGuard VPN connection."""
    active = get_active_wireguard()
    if not active:
        return "No WireGuard interface is currently active"

    success, message = wireguard_down(active)
    return message


def _download_video_internal(
    url: str,
    auto_vpn: bool = True,
    preferred_city: Optional[str] = None,
    output_dir: Optional[str] = None,
    format_spec: str = "best"
) -> str:
    """Internal function to perform actual download (used by both direct calls and queue)."""
    output_path = Path(output_dir) if output_dir else DEFAULT_DOWNLOAD_DIR
    output_path.mkdir(parents=True, exist_ok=True)

    vpn_started = False
    original_vpn = get_active_wireguard()

    try:
        # Handle VPN if requested
        if auto_vpn and not original_vpn:
            country = detect_url_country(url)

            if country:
                config = select_best_config(country=country, preferred_city=preferred_city)

                if config:
                    success, msg = wireguard_up(config)
                    if success:
                        vpn_started = True
                        vpn_msg = f"Started VPN: {config.stem} (detected country: {country})"
                    else:
                        vpn_msg = f"VPN start failed: {msg}"
                else:
                    vpn_msg = f"No VPN config found for {country}, proceeding without VPN"
            else:
                vpn_msg = "No VPN needed for this URL (generic/YouTube)"
        else:
            vpn_msg = f"Using existing VPN: {original_vpn}" if original_vpn else "Proceeding without VPN"

        # Run yt-dlp
        cmd = [
            "yt-dlp",
            "-f", format_spec,
            "-o", str(output_path / "%(title)s.%(ext)s"),
            url
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600  # 10 minute timeout
        )

        if result.returncode == 0:
            return f"{vpn_msg}\n\nDownload successful!\n{result.stdout}"
        else:
            return f"{vpn_msg}\n\nDownload failed:\n{result.stderr}"

    except subprocess.TimeoutExpired:
        return f"{vpn_msg}\n\nDownload timed out after 10 minutes"
    except Exception as e:
        return f"{vpn_msg}\n\nError: {str(e)}"
    finally:
        # Clean up VPN if we started it
        if vpn_started and not original_vpn:
            active = get_active_wireguard()
            if active:
                wireguard_down(active)


@mcp.tool()
def queue_download(
    url: str,
    auto_vpn: bool = True,
    preferred_city: Optional[str] = None,
    output_dir: Optional[str] = None,
    format_spec: str = "best"
) -> str:
    """
    Add a video download to the queue. Downloads are processed sequentially.

    Args:
        url: Video URL to download
        auto_vpn: Automatically select and start VPN based on URL (default: True)
        preferred_city: Preferred VPN city (e.g., 'lon', 'nyc', 'tor')
        output_dir: Download directory (default: ~/Downloads)
        format_spec: yt-dlp format specification (default: 'best')
    """
    job = download_queue.add(url, auto_vpn, preferred_city, output_dir, format_spec)

    detected_country = detect_url_country(url) if auto_vpn else None
    country_msg = f" (detected: {detected_country})" if detected_country else ""

    return f"Added to queue: Job #{job.id}{country_msg}\nURL: {url}\nUse 'queue_status()' to monitor progress"


@mcp.tool()
def queue_status() -> str:
    """Check the status of the download queue."""
    status = download_queue.get_status()

    lines = []

    # Current job
    if status["current"]:
        job = status["current"]
        lines.append(f"Currently downloading: Job #{job['id']}")
        lines.append(f"  URL: {job['url']}")
        lines.append(f"  Started: {job['started_at']}")
        lines.append("")

    # Queued jobs
    if status["queued"]:
        lines.append(f"Queued jobs ({len(status['queued'])}):")
        for job in status["queued"]:
            lines.append(f"  #{job['id']}: {job['url']}")
        lines.append("")
    else:
        if not status["current"]:
            lines.append("Queue is empty")
            lines.append("")

    # Recent history
    if status["recent_history"]:
        lines.append(f"Recent completions (last {len(status['recent_history'])}):")
        for job in status["recent_history"]:
            status_icon = "✓" if job['status'] == "completed" else "✗"
            lines.append(f"  {status_icon} #{job['id']}: {job['url']}")
            if job.get('error'):
                lines.append(f"     Error: {job['error']}")

    return "\n".join(lines) if lines else "Queue is empty"


@mcp.tool()
def queue_cancel(job_id: int) -> str:
    """
    Cancel a queued download job.

    Args:
        job_id: The job ID to cancel (from queue_status)
    """
    if download_queue.cancel(job_id):
        return f"Job #{job_id} has been cancelled"
    return f"Job #{job_id} not found in queue (may have already started or completed)"


@mcp.tool()
def queue_clear_history() -> str:
    """Clear the download history (keeps current/queued jobs)."""
    download_queue.clear_history()
    return "Download history cleared"


@mcp.tool()
def get_video_info(url: str) -> str:
    """
    Get information about a video without downloading it.

    Args:
        url: Video URL to inspect
    """
    try:
        result = subprocess.run(
            ["yt-dlp", "--dump-json", url],
            capture_output=True,
            text=True,
            check=True,
            timeout=30
        )

        # Parse and format key info
        import json
        info = json.loads(result.stdout)

        output = []
        output.append(f"Title: {info.get('title', 'N/A')}")
        output.append(f"Uploader: {info.get('uploader', 'N/A')}")
        output.append(f"Duration: {info.get('duration', 0)} seconds")
        output.append(f"View count: {info.get('view_count', 'N/A')}")

        if "formats" in info:
            output.append(f"Available formats: {len(info['formats'])}")

        # Check for geo-restrictions
        if info.get("geo_bypass_country"):
            output.append(f"Geo-restriction detected: {info['geo_bypass_country']}")

        return "\n".join(output)

    except subprocess.CalledProcessError as e:
        return f"Failed to get video info:\n{e.stderr}"
    except subprocess.TimeoutExpired:
        return "Request timed out"
    except Exception as e:
        return f"Error: {str(e)}"


if __name__ == "__main__":
    mcp.run()
