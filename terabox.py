import asyncio
import re
from urllib.parse import parse_qs, urlparse

import aiohttp

from tools import get_formatted_size


# ---------------- URL VALIDATION ---------------- #

def check_url_patterns(url):
    patterns = [
        r"terabox\.com",
        r"terabox\.app",
        r"terabox\.fun",
        r"terabox\.best",
        r"terabox\.ap",
        r"terabox\.club",
        r"terabox\.click",
        r"teraboxapp\.com",
        r"teraboxlink\.com",
        r"teraboxlinke\.com",
        r"teraboxshare\.com",
        r"teraboxsharefile\.com",
        r"teraboxurl\.com",
        r"teraboxfree\.com",
        r"terasharelink\.com",
        r"terasharefile\.com",
        r"terashareus\.com",
        r"terafileshare\.com",
        r"tera1024box\.com",
        r"1024tera\.com",
        r"1024tera\.co",
        r"1024terabox\.com",
        r"1024-terabox\.com",
        r"4funbox\.com",
        r"4funbox\.co",
        r"4funbox\.in",
        r"mirrobox\.com",
        r"nephobox\.com",
        r"freeterabox\.com",
        r"momerybox\.com",
        r"tibibox\.com",
        r"gibibox\.com",
        r"pebibox\.com",
        r"fancybox\.in",
        r"bestclouddrive\.com",
        r"dubox\.com",
        r"playduo\.link",
        r"theteraboxmod\.app",
    ]

    return any(re.search(pattern, url) for pattern in patterns)


def get_urls_from_string(string: str) -> list[str]:
    urls = re.findall(r"(https?://\S+)", string)
    urls = [url.rstrip(".,)>]}") for url in urls if check_url_patterns(url)]
    return urls[0] if urls else []


# ---------------- TERABOX HELPERS ---------------- #

def extract_surl(url: str):
    parsed = urlparse(url)

    # ?surl=XXXX
    query_surl = parse_qs(parsed.query).get("surl")
    if query_surl:
        key = query_surl[0]
    else:
        # /s/XXXX
        match = re.search(r"/s/([A-Za-z0-9_-]+)", parsed.path)
        if not match:
            match = re.search(r"/s/([A-Za-z0-9_-]+)", url)

        if not match:
            return None

        key = match.group(1)

    # TeraBox share/list generally expects shorturl without leading 1
    if key.startswith("1"):
        return key[1:]

    return key


def extract_jstoken(html: str):
    patterns = [
        r'fn%28%22(.*?)%22%29',
        r'fn\("([^"]+)"\)',
        r'jsToken\s*=\s*["\']([^"\']+)["\']',
        r'jsToken["\']?\s*:\s*["\']([^"\']+)["\']',
        r'window\.jsToken\s*=\s*["\']([^"\']+)["\']',
        r'window\.jsToken.*?%22(.*?)%22',
    ]

    for pattern in patterns:
        match = re.search(pattern, html)
        if match and match.group(1):
            return match.group(1)

    return None


# ---------------- HTTP ---------------- #

async def fetch(session, url, **kwargs):
    timeout = aiohttp.ClientTimeout(
        total=30,
        connect=10,
        sock_read=20
    )

    for attempt in range(3):
        try:
            async with session.get(
                url,
                timeout=timeout,
                **kwargs
            ) as response:

                text = await response.text()

                if response.status == 200:
                    return text

                print(
                    f"[Retry {attempt + 1}] "
                    f"HTTP {response.status}"
                )

        except Exception as e:
            print(
                f"[Retry {attempt + 1}] "
                f"Error: {e}"
            )

        await asyncio.sleep(2)

    return None


# ---------------- MAIN ---------------- #

async def get_files(url: str):
    """
    Direct TeraBox extraction.
    No api.ntm.com required.
    """

    shorturl = extract_surl(url)

    if not shorturl:
        print("Could not extract TeraBox shorturl")
        return False

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/145.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    async with aiohttp.ClientSession(
        headers=headers
    ) as session:

        # Get TeraBox share page
        share_url = (
            f"https://dm.terabox.app/"
            f"sharing/link?surl=1{shorturl}"
        )

        print("\nREQUESTING TERABOX:", share_url)

        html = await fetch(
            session,
            share_url
        )

        if not html:
            print("Failed to load TeraBox share page")
            return False

        # Extract jsToken
        js_token = extract_jstoken(html)

        if not js_token:
            print("jsToken not found")
            return False

        print("jsToken extracted successfully")

        # TeraBox share/list API
        api_url = "https://dm.terabox.app/share/list"

        params = {
            "app_id": "250528",
            "jsToken": js_token,
            "site_referer": "https://www.terabox.app/",
            "shorturl": shorturl,
            "root": "1",
        }

        api_headers = {
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": share_url,
            "Origin": "https://dm.terabox.app",
        }

        try:
            async with session.get(
                api_url,
                params=params,
                headers=api_headers,
                timeout=30
            ) as response:

                print("TERABOX API STATUS:", response.status)

                data = await response.json()

        except Exception as e:
            print("TeraBox API error:", e)
            return False

        print("TERABOX RESPONSE:", data)

        if data.get("errno") not in [0, "0"]:
            print(
                "TeraBox returned error:",
                data.get("errmsg", data.get("message"))
            )
            return False

        files = data.get("list", [])

        if not files:
            print("No files found")
            return False

        result = []

        for file in files:

            # Ignore folders for now
            if str(file.get("isdir", "0")) == "1":
                continue

            download_url = (
                file.get("dlink")
                or file.get("download_url")
            )

            if not download_url:
                continue

            size_bytes = int(
                file.get("size", 0) or 0
            )

            result.append({
                "file_name": file.get(
                    "server_filename",
                    "TeraBox File"
                ),
                "size": get_formatted_size(size_bytes),
                "sizebytes": size_bytes,
                "thumb": file.get("thumbs", {}).get("url3"),
                "direct_link": download_url,
                "link": download_url,
            })

        if not result:
            print("No direct download links found")
            return False

        print(
            f"Found {len(result)} downloadable file(s)"
        )

        return result


async def get_data(url: str):
    """Fetch first TeraBox file."""
    files = await get_files(url)

    if not files:
        return False

    return files[0]
