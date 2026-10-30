"""Sign in with Steam (OpenID 2.0) and Steam display names.

Steam signs the user in on its own site and redirects back with an assertion;
the server then asks Steam directly whether that assertion is genuine
(``check_authentication``), so it can't be forged by the browser.
"""

from __future__ import annotations

import re
from urllib.parse import urlencode

import httpx

OPENID_URL = "https://steamcommunity.com/openid/login"
_NS = "http://specs.openid.net/auth/2.0"
_CLAIMED_ID = re.compile(r"^https://steamcommunity\.com/openid/id/(\d{17})$")


def login_url(return_to: str, realm: str) -> str:
    return OPENID_URL + "?" + urlencode({
        "openid.ns": _NS,
        "openid.mode": "checkid_setup",
        "openid.return_to": return_to,
        "openid.realm": realm,
        "openid.identity": f"{_NS}/identifier_select",
        "openid.claimed_id": f"{_NS}/identifier_select",
    })


def verify(params: dict[str, str], expected_return_to: str) -> str | None:
    """The 64-bit Steam ID if Steam confirms the assertion, else None."""
    match = _CLAIMED_ID.match(params.get("openid.claimed_id", ""))
    if not match or params.get("openid.mode") != "id_res":
        return None
    if params.get("openid.op_endpoint") != OPENID_URL:
        return None
    # The assertion must be for our callback, not one made for another site.
    if params.get("openid.return_to", "").split("?")[0] != expected_return_to.split("?")[0]:
        return None
    check = {k: v for k, v in params.items() if k.startswith("openid.")}
    check["openid.mode"] = "check_authentication"
    try:
        r = httpx.post(OPENID_URL, data=check, timeout=10)
    except httpx.HTTPError:
        return None
    if "is_valid:true" not in r.text.replace(" ", ""):
        return None
    return match.group(1)


def display_name(steam_id: str, api_key: str | None) -> str | None:
    """The user's Steam display name: from the Web API with a key, otherwise from
    the public profile page (which gives the name even for private profiles)."""
    if api_key:
        try:
            r = httpx.get(
                "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/",
                params={"key": api_key, "steamids": steam_id}, timeout=10,
            )
            players = r.json()["response"]["players"]
            if players:
                return players[0]["personaname"]
        except (httpx.HTTPError, KeyError, ValueError, IndexError):
            pass
    try:
        r = httpx.get(f"https://steamcommunity.com/profiles/{steam_id}/",
                      params={"xml": 1}, timeout=10)
    except httpx.HTTPError:
        return None
    return profile_name(r.text)


_PROFILE_NAME = re.compile(r"<steamID><!\[CDATA\[(.*?)\]\]></steamID>", re.S)


def profile_name(xml: str) -> str | None:
    """The name in a profile's ``?xml=1`` page (read with a regex: no XML parser
    on a page from elsewhere)."""
    match = _PROFILE_NAME.search(xml)
    name = match.group(1).strip()[:64] if match else ""
    return name or None
