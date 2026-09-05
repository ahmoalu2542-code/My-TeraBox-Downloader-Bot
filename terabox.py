import asyncio
import re
from urllib.parse import parse_qs, urlparse

import aiohttp

from config import TERABOX_API_TEMPLATE
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

    for pattern in patterns:
        if re.search(pattern, url):
            return True

    return False


def get_urls_from_string(string: str) -> list[str]:
    pattern = r"(https?://\S+)"
    urls = re.findall(pattern, string)
    urls = [url for url in urls if check_url_patterns(url)]
    if not urls:
        return []
    return urls[0]


def extract_surl_from_url(url: str) -> str | None:
    parsed_url = urlparse(url)
    query_params = parse_qs(parsed_url.query)
    surl = query_params.get("surl", [])
    return surl[0] if surl else False


# ---------------- TeraBox SHARE API ---------------- #

# Current TeraBox share pages redirect from dm.terabox.app to www.terabox.app.
# We follow that redirect, extract the jsToken from the returned HTML, then
# call the share/list endpoint and convert its response to the format used
# by main.py.

SHARE_PAGE_URL = "https://dm.terabox.app/sharing/link"


def _extract_share_key(url: str):
    """Return (surl_param, shorturl) for TeraBox share/list."""
    parsed = urlparse(url)

    # Normal links: https://terabox.com/s/1ABC...
    path_match = re.search(r"/s/([A-Za-z0-9_-]+)", parsed.path)
    key = path_match.group(1) if path_match else None

    # Some links use ?surl=1ABC...
    if not key:
        values = parse_qs(parsed.query).get("surl", [])
        if values:
            key = values[0]

    if not key:
        return None, None

    # share/list wants the shorturl without the leading "1".
    if key.startswith("1"):
        return key, key[1:]
    return "1" + key, key


def _extract_js_token(html: str):
    """Extract jsToken from current TeraBox HTML, encoded or decoded."""
    # The page currently contains a URL-encoded JS snippet such as:
    # fn%28%22TOKEN%22%29
    patterns = [
        r"fn%28%22([^%]+)%22%29",
        r'fn\("([^"]+)"\)',
        r'window\.jsToken\s*=\s*["\']([^"\']+)["\']',
        r'jsToken\s*=\s*["\']([^"\']+)["\']',
        r'jsToken["\']?\s*:\s*["\']([^"\']+)["\']',
    ]

    for pattern in patterns:
        match = re.search(pattern, html)
        if match and match.group(1):
            return match.group(1)

    # Decode URL-encoded HTML and try again.
    try:
        from urllib.parse import unquote
        decoded = unquote(html)
    except Exception:
        decoded = html

    for pattern in patterns[1:]:
        match = re.search(pattern, decoded)
        if match and match.group(1):
            return match.group(1)

    return None


async def _fetch_share_page(session, surl_param):
    """Fetch share page and return (html, final_url)."""
    url = f"{SHARE_PAGE_URL}?surl={surl_param}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/145.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.terabox.app/",
    }

    async with session.get(url, headers=headers, allow_redirects=True) as resp:
        html = await resp.text()
        print("SHARE PAGE STATUS:", resp.status)
        print("SHARE PAGE URL:", str(resp.url))
        return html, str(resp.url)


async def _fetch_file_list(session, shorturl, js_token, base_url):
    """Call TeraBox share/list and return its JSON."""
    # Prefer the host reached after the redirect; fall back to dm if needed.
    parsed = urlparse(base_url)
    host = parsed.netloc or "www.terabox.app"
    api_urls = [
        f"https://{host}/share/list",
        "https://dm.terabox.app/share/list",
    ]

    params = {
        "app_id": "250528",
        "web": "1",
        "channel": "dubox",
        "clienttype": "0",
        "jsToken": js_token,
        "site_referer": "https://www.terabox.app/",
        "shorturl": shorturl,
        "root": "1",
        "page": "1",
        "num": "1000",
        "by": "name",
        "order": "asc",
    }

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/145.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": base_url,
        "Origin": f"https://{host}",
    }

    last_error = None

    for api_url in api_urls:
        try:
            print("\nREQUESTING TERABOX API:", api_url)
            async with session.get(
                api_url,
                params=params,
                headers=headers,
                allow_redirects=True,
            ) as resp:
                raw = await resp.text()
                print("SHARE API STATUS:", resp.status)

                if resp.status != 200:
                    last_error = f"HTTP {resp.status}: {raw[:300]}"
                    continue

                try:
                    return await resp.json(content_type=None)
                except Exception:
                    import json
                    return json.loads(raw)

        except Exception as e:
            last_error = str(e)
            print("SHARE API ERROR:", e)

    print("Share API failed:", last_error)
    return None


# ---------------- MAIN API HANDLER ---------------- #

async def get_files(url: str):
    """Async: Fetch all files from a TeraBox share link."""
    if not url:
        return False

    surl_param, shorturl = _extract_share_key(url)
    if not surl_param or not shorturl:
        print("Could not extract TeraBox surl")
        return False

    timeout = aiohttp.ClientTimeout(total=45, connect=15, sock_read=30)
    connector = aiohttp.TCPConnector(ssl=True)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/145.0.0.0 Safari/537.36"
        )
    }

    async with aiohttp.ClientSession(
        timeout=timeout,
        connector=connector,
        headers=headers,
    ) as session:

        # Try the share page twice in case TeraBox rotates the token.
        js_token = None
        final_url = "https://www.terabox.app/"

        for attempt in range(1, 3):
            try:
                print(
                    f"\nREQUESTING TERABOX SHARE PAGE "
                    f"(attempt {attempt}): {SHARE_PAGE_URL}?surl={surl_param}"
                )
                html, final_url = await _fetch_share_page(session, surl_param)
                js_token = _extract_js_token(html)

                if js_token:
                    print("jsToken extracted successfully.")
                    break

                print("jsToken not found in share page.")

            except Exception as e:
                print(f"[Share page attempt {attempt}] Error:", e)

            await asyncio.sleep(1)

        if not js_token:
            print("Failed to extract jsToken from TeraBox share page")
            return False

        data = await _fetch_file_list(
            session,
            shorturl,
            js_token,
            final_url,
        )

        if not data:
            return False

        print("TERABOX API RAW RESPONSE:", data)

        # Current TeraBox share/list response uses "list".
        files = data.get("list") or data.get("files")
        if not files:
            print(
                "No files in TeraBox response. "
                f"errno={data.get('errno')} errmsg={data.get('errmsg')}"
            )
            return False

        result = []

        for f in files:
            # Ignore directories; main.py expects downloadable files.
            if str(f.get("isdir", "0")) == "1":
                continue

            direct_link = (
                f.get("dlink")
                or f.get("download_url")
                or f.get("downloadUrl")
            )

            if not direct_link:
                continue

            try:
                size_bytes = int(f.get("size") or 0)
            except (TypeError, ValueError):
                size_bytes = 0

            filename = (
                f.get("server_filename")
                or f.get("filename")
                or f.get("name")
                or "TeraBox_File"
            )

            thumb = f.get("thumbs", {}).get("url3") if isinstance(
                f.get("thumbs"), dict
            ) else f.get("thumb")

            result.append({
                "file_name": filename,
                "size": get_formatted_size(size_bytes),
                "sizebytes": size_bytes,
                "thumb": thumb,
                "direct_link": direct_link,
                "link": direct_link,
            })

        if not result:
            print("No valid download links in TeraBox response")
            return False

        print(f"FOUND {len(result)} DOWNLOADABLE FILE(S)")
        return result


async def get_data(url: str):
    """Async: Fetch the FIRST TeraBox file only."""
    files = await get_files(url)
    if not files:
        return False
    return files[0]
