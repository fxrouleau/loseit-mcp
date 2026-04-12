"""HTTP transport layer: shared session, device headers, auth injection."""
from __future__ import annotations

import requests

from .auth import Auth, DEFAULT_HEADERS

GATEWAY_HOST = "https://gateway.loseit.com"
SYNC_HOST = "https://sync.loseit.com"
FOOD_SEARCH_HOST = "https://food-search.prod.fitnowinc.com"
BARCODE_API_KEY = "70D980AE-FEA1-4058-82EC-E5C5FC229647"


class Transport:
    def __init__(self, auth: Auth) -> None:
        self.auth = auth
        self.session = auth.session

    def _headers(
        self,
        *,
        content_type: str | None = None,
        for_gateway: bool = False,
    ) -> dict[str, str]:
        """Build headers matching the captured native app.

        The gateway routes expect `x-loseit-device` (note: this differs
        from the login `/account/login` route which wants `x-fitnow-deviceid`)
        plus a `loseitlocale=en-US` cookie and
        `content-type: application/octet-stream; charset=utf-8`.
        """
        tokens = self.auth.ensure_fresh()
        h = dict(DEFAULT_HEADERS)
        h["authorization"] = f"Bearer {tokens.access_token}"
        if tokens.device_id:
            if for_gateway:
                h["x-loseit-device"] = tokens.device_id
            else:
                h["x-fitnow-deviceid"] = tokens.device_id
        if for_gateway:
            h["cookie"] = "loseitlocale=en-US"
        if content_type:
            h["content-type"] = content_type
        return h

    def post_transaction_bundle(self, bundle_bytes: bytes) -> bytes:
        url = f"{GATEWAY_HOST}/user/loseItTransactionBundle"
        r = self.session.post(
            url,
            headers=self._headers(
                content_type="application/octet-stream; charset=utf-8",
                for_gateway=True,
            ),
            data=bundle_bytes,
        )
        r.raise_for_status()
        return r.content

    def get_user_database(self) -> bytes:
        """Download the user's SQLite database snapshot."""
        url = f"{GATEWAY_HOST}/user/database?newschema"
        r = self.session.post(
            url,
            headers=self._headers(
                content_type="application/octet-stream; charset=utf-8",
                for_gateway=True,
            ),
            data=b"",
        )
        r.raise_for_status()
        return r.content

    def barcode_lookup(self, barcode: str, locale: str = "en_US") -> bytes:
        """Barcode lookup uses a different host and a static API key, not
        the user JWT."""
        url = f"{FOOD_SEARCH_HOST}/food/barcode"
        headers = dict(DEFAULT_HEADERS)
        headers["x-api-key"] = BARCODE_API_KEY
        r = self.session.get(
            url,
            headers=headers,
            params={"barcode": barcode, "preferred_locale": locale},
        )
        r.raise_for_status()
        return r.content

    def text_food_search(
        self,
        query: str,
        *,
        brand: str = "",
        locale: str = "en-US",
        limit: int = 20,
    ) -> bytes:
        """Full-catalog text search (returns `FoodSearchResponse` proto).

        Locale must use hyphen (`en-US`), not underscore. Omit `brand`
        entirely when empty — passing `brand=""` makes the server 500.
        """
        url = f"{FOOD_SEARCH_HOST}/food/search"
        headers = dict(DEFAULT_HEADERS)
        headers["x-api-key"] = BARCODE_API_KEY
        params: dict[str, str | int] = {"q": query, "locale": locale, "limit": limit}
        if brand:
            params["brand"] = brand
        r = self.session.get(url, headers=headers, params=params)
        r.raise_for_status()
        return r.content
