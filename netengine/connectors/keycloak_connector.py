"""Keycloak OIDC admin API connector with token caching."""

from typing import Any, Dict, Optional

import aiohttp
from loguru import logger

from netengine.connectors.base import Connector


class KeycloakConnector(Connector):
    """Manages Keycloak admin API access with token caching."""

    def __init__(
        self,
        keycloak_url: str,
        admin_username: str,
        admin_password: str,
    ) -> None:
        self.keycloak_url = keycloak_url.rstrip("/")
        self.admin_username = admin_username
        self.admin_password = admin_password
        self._session: Optional[aiohttp.ClientSession] = None
        self._access_token: Optional[str] = None
        self._timeout = aiohttp.ClientTimeout(total=8)

    async def connect(self) -> None:
        """Initialize HTTP session."""
        connector = aiohttp.TCPConnector(ssl=False)
        self._session = aiohttp.ClientSession(connector=connector, timeout=self._timeout)
        logger.info("Keycloak connector connected")

    async def disconnect(self) -> None:
        """Close HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None
            self._access_token = None
            logger.info("Keycloak connector disconnected")

    async def health(self) -> bool:
        """Check Keycloak health endpoint."""
        if not self._session:
            return False
        try:
            health_url = f"{self.keycloak_url}/health/ready"
            async with self._session.get(health_url) as resp:
                return resp.status in (200, 204)
        except Exception as e:
            logger.warning(f"Keycloak health check failed: {e}")
            return False

    async def _get_admin_token(self) -> str:
        """Acquire admin bearer token via password grant."""
        if not self._session:
            raise RuntimeError("Keycloak connector not connected")

        token_url = f"{self.keycloak_url}/realms/master/protocol/openid-connect/token"
        data = {
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": self.admin_username,
            "password": self.admin_password,
        }

        async with self._session.post(token_url, data=data) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Token request failed: {resp.status}")
            result = await resp.json()
            return result["access_token"]

    async def _admin_request(
        self, method: str, path: str, json: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Make authenticated admin API request."""
        if not self._session:
            raise RuntimeError("Keycloak connector not connected")

        if not self._access_token:
            self._access_token = await self._get_admin_token()

        headers = {"Authorization": f"Bearer {self._access_token}"}
        url = f"{self.keycloak_url}{path}"

        async with self._session.request(method, url, json=json, headers=headers) as resp:
            if resp.status == 401:
                self._access_token = await self._get_admin_token()
                headers["Authorization"] = f"Bearer {self._access_token}"
                async with self._session.request(
                    method, url, json=json, headers=headers
                ) as retry_resp:
                    if retry_resp.status >= 400:
                        raise RuntimeError(f"Admin request failed: {retry_resp.status}")
                    return await retry_resp.json() if retry_resp.content_length else {}

            if resp.status >= 400:
                raise RuntimeError(f"Admin request failed: {resp.status}")
            return await resp.json() if resp.content_length else {}

    async def create_realm(self, realm_name: str) -> None:
        """Create a Keycloak realm."""
        await self._admin_request("POST", "/admin/realms", json={"realm": realm_name})

    async def create_user(
        self, realm: str, username: str, password: str, email: str
    ) -> str:
        """Create a user in a realm."""
        user_data = {
            "username": username,
            "email": email,
            "enabled": True,
            "credentials": [{"type": "password", "value": password, "temporary": False}],
        }
        await self._admin_request(
            "POST", f"/admin/realms/{realm}/users", json=user_data
        )
        users = await self._admin_request(
            "GET", f"/admin/realms/{realm}/users?username={username}"
        )
        return users[0]["id"] if users else ""

    async def create_client(self, realm: str, client_id: str) -> str:
        """Create an OIDC client in a realm."""
        client_data = {
            "clientId": client_id,
            "enabled": True,
            "publicClient": False,
            "standardFlowEnabled": True,
        }
        await self._admin_request(
            "POST", f"/admin/realms/{realm}/clients", json=client_data
        )
        clients = await self._admin_request(
            "GET", f"/admin/realms/{realm}/clients?clientId={client_id}"
        )
        return clients[0]["id"] if clients else ""
