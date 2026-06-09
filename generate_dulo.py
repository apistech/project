import os
import re
import sys

import cloudscraper

# ── Config ────────────────────────────────────────────────────────────────────
OUTPUT_DIR   = "playlists"
M3U_OUT      = os.path.join(OUTPUT_DIR, "dulo.m3u")

CHANNELS_API = "https://dulo.tv/api/live-tv/channels"

# ── Helpers ───────────────────────────────────────────────────────────────────

def fetch_channels() -> list[dict]:
    print("Fetching channel list from dulo.tv ...")
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )
    r = scraper.get(CHANNELS_API, timeout=30)
    if r.status_code != 200:
        print(f"  [error] HTTP {r.status_code} from dulo.tv")
        print(f"  Response: {r.text[:300]}")
        sys.exit(1)
    data = r.json()
    channels = data.get("channels", data) if isinstance(data, dict) else data
    print(f"  → {len(channels)} channels")
    return channels


def build_m3u(channels: list[dict]) -> str:
    lines = ["#EXTM3U\n"]

    valid = [ch for ch in channels if ch.get("source_url", "")]
    sorted_channels = sorted(
        valid,
        key=lambda ch: (ch.get("category", "General").title().lower(), ch.get("name", "").lower())
    )

    for ch in sorted_channels:
        ch_id  = ch.get("id", "")
        name   = ch.get("name", "Unknown")
        logo   = ch.get("logo_url", "")
        group  = ch.get("category", "General").title()
        stream = ch.get("source_url", "")

        lines.append(
            f'#EXTINF:-1 tvg-id="{ch_id}" tvg-name="{name}" '
            f'tvg-logo="{logo}" group-title="{group}",{name}\n'
            f'{stream}\n'
        )
    return "".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    channels = fetch_channels()

    print("\nBuilding M3U playlist ...")
    m3u_content = build_m3u(channels)
    with open(M3U_OUT, "w", encoding="utf-8") as f:
        f.write(m3u_content)
    print(f"  → wrote {M3U_OUT} ({len(m3u_content):,} bytes)")

    print("\nDone.")


if __name__ == "__main__":
    main()
