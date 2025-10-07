# YT-DLP MCP Server with WireGuard VPN

MCP server that automatically manages WireGuard VPN connections and downloads videos using yt-dlp. Perfect for resource-constrained devices like Raspberry Pi.

## Features

- **Smart country detection**: Automatically detects target country from URL (BBC iPlayer → UK, CBC → Canada, etc.)
- **Multi-country VPN support**: Works with WireGuard configs from any country (UK, US, CA, AU, etc.)
- **Download queue**: Sequential processing of multiple downloads - perfect for Raspberry Pi
- **WireGuard management**: Start/stop VPN connections via MCP tools
- **Video downloads**: Download videos through yt-dlp with automatic VPN routing
- **Video info**: Inspect video metadata without downloading

## Prerequisites

- Python 3.10+
- yt-dlp installed: `sudo apt install yt-dlp` or `pip install yt-dlp`
- WireGuard installed: `sudo apt install wireguard wireguard-tools`
- WireGuard configs in `/etc/wireguard/`
- Sudo access for WireGuard commands (see setup below)

## Installation

```bash
# Install dependencies with uv (fast Python package installer)
uv pip install -e .

# Or if you prefer requirements.txt style
uv pip install -r requirements.txt

# Configure sudo permissions for WireGuard (see below)
```

**Note**: This project uses `uv` for faster dependency management. If you don't have `uv` installed:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Sudo Configuration

The server needs to run `wg-quick up/down` without password prompts. Add this to `/etc/sudoers` using `sudo visudo`:

```
# Replace 'username' with your actual username
username ALL=(ALL) NOPASSWD: /usr/bin/wg-quick
```

## Usage

### Running the Server

```bash
# Run directly with Python
python server.py

# Or with uv
uv run server.py
```

Or add to your Claude Desktop MCP config:
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Linux**: `~/.config/claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "yt-dlp-vpn": {
      "command": "uv",
      "args": ["run", "/home/ohffs/yt-api/server.py"]
    }
  }
}
```

Or using Python directly (if dependencies are already installed):
```json
{
  "mcpServers": {
    "yt-dlp-vpn": {
      "command": "python",
      "args": ["/home/ohffs/yt-api/server.py"]
    }
  }
}
```

### Available Tools

#### Queue Management (Recommended for Multiple Downloads)

##### `queue_download(url: str, auto_vpn?: bool, preferred_city?: str, output_dir?: str, format_spec?: str)`
Add a video download to the queue. Downloads are processed sequentially.
- `url`: Video URL
- `auto_vpn`: Auto-select VPN based on URL (default: True)
- `preferred_city`: Preferred VPN city (e.g., 'lon', 'nyc', 'tor')
- `output_dir`: Download location (default: ~/Downloads)
- `format_spec`: yt-dlp format (default: 'best')

##### `queue_status()`
Check download queue status - current job, queued jobs, and recent history.

##### `queue_cancel(job_id: int)`
Cancel a queued download job by ID.

##### `queue_clear_history()`
Clear the download history (keeps active/queued jobs).

#### WireGuard Management

##### `list_wireguard_configs()`
List all available WireGuard configurations grouped by country.

##### `wireguard_status()`
Check if any WireGuard VPN is currently active.

##### `start_wireguard(config_name?: str, country?: str, city?: str)`
Start a WireGuard VPN connection.
- `config_name`: Specific config (e.g., 'gb-lon-wg-001'), or auto-select
- `country`: Country code (e.g., 'gb', 'us', 'ca')
- `city`: Preferred city code (e.g., 'lon', 'nyc', 'tor')

##### `stop_wireguard()`
Stop the currently active WireGuard VPN.

#### Video Tools

##### `get_video_info(url: str)`
Get video metadata without downloading.

## Examples

### Queue-based Downloads (Recommended)

```python
# Add multiple downloads to queue - they'll process sequentially
queue_download("https://www.bbc.co.uk/iplayer/episode/...")  # Auto-detects UK, uses GB VPN
queue_download("https://www.cbc.ca/player/play/...")         # Auto-detects Canada, uses CA VPN
queue_download("https://www.abc.net.au/iview/...")           # Auto-detects Australia, uses AU VPN

# Check queue status
queue_status()

# Cancel a job
queue_cancel(2)

# Clear completed jobs from history
queue_clear_history()
```

### URL Detection Examples

The server automatically detects the target country from URLs:

- **UK**: `bbc.co.uk/iplayer`, `itv.com`, `channel4.com` → `gb`
- **US**: `hulu.com`, `nbc.com`, `hbo.com` → `us`
- **Canada**: `cbc.ca`, `ctv.ca` → `ca`
- **Australia**: `abc.net.au`, `sbs.com.au`, `9now.com.au` → `au`
- **YouTube**: No VPN (direct connection)

### Manual VPN Management

```python
# List available configs by country
list_wireguard_configs()

# Start specific country VPN
start_wireguard(country="gb")

# Start with city preference
start_wireguard(country="us", city="nyc")

# Start specific config
start_wireguard(config_name="gb-lon-wg-001")

# Check status
wireguard_status()

# Stop VPN
stop_wireguard()
```

### Video Info

```python
# Get video info before downloading
get_video_info("https://www.bbc.co.uk/iplayer/episode/...")
```

## Configuration Files

Your WireGuard configs are auto-detected from `/etc/wireguard/`.

**Filename format**: `{country}-{city}-wg-{number}.conf`

Examples:
- `gb-lon-wg-001.conf` - UK, London
- `us-nyc-wg-001.conf` - US, New York
- `ca-tor-wg-001.conf` - Canada, Toronto
- `au-syd-wg-001.conf` - Australia, Sydney

The server automatically groups configs by country and selects the best match based on the URL being downloaded.

## Troubleshooting

**"Failed to start WireGuard"**
- Check sudo permissions are configured correctly
- Verify WireGuard is installed: `which wg-quick`
- Test manually: `sudo wg-quick up au-syd-wg-001`

**"Download failed"**
- Ensure yt-dlp is installed: `which yt-dlp`
- Check URL is valid
- Try getting video info first with `get_video_info()`

**"No WireGuard configs found"**
- Verify configs exist in `/etc/wireguard/`
- Check file permissions allow reading

## Supported Country Detection

The server includes heuristics for detecting these regions:

- **UK** (`gb`): BBC iPlayer, ITV, Channel 4/5, .uk domains
- **US** (`us`): Hulu, NBC, ABC, CBS, Fox, HBO, Peacock, .us domains
- **Canada** (`ca`): CBC, CTV, Global TV, .ca domains
- **Australia** (`au`): ABC iView, SBS, 9Now, 10Play, 7Plus, .au domains
- **Europe**: Germany (`de`), France (`fr`), Italy (`it`), Spain (`es`), Netherlands (`nl`), Sweden (`se`), Norway (`no`), Denmark (`dk`)
- **Asia**: Japan (`jp`), Korea (`kr`), Singapore (`sg`), Hong Kong (`hk`)
- **Other**: New Zealand (`nz`)

**Note**: YouTube and generic domains use direct connection (no VPN) by default.

## How It Works

1. **URL Analysis**: Detects target country from domain and URL patterns
2. **Config Selection**: Finds matching WireGuard config for that country
3. **VPN Management**: Starts appropriate VPN before download, stops after
4. **Queue Processing**: Handles multiple downloads sequentially (perfect for Raspberry Pi)
5. **Auto-cleanup**: Always stops VPN after download completes

## Security Notes

- WireGuard requires root/sudo privileges
- Only use on trusted systems
- Keep WireGuard configs secure
- Be mindful of VPN provider terms of service
- Respect content licensing and regional restrictions
