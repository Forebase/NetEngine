import aiohttp
import asyncio
from typing import Optional

class OIDCHandler:
    def __init__(self, keycloak_url: str, admin_username: str, admin_password: str):
        self.keycloak_url = keycloak_url.rstrip("/")
        self.admin_username = admin_username
        self.admin_password = admin_password
        self._access_token: Optional[str] = None

    async def _get_admin_token(self) -> str:
        if self._access_token:
            return self._access_token
        # Get token via client credentials (use the admin-cli client)
        token_url = f"{self.keycloak_url}/realms/master/protocol/openid-connect/token"
        data = {
            "client_id": "admin-cli",
            "username": self.admin_username,
            "password": self.admin_password,
            "grant_type": "password"
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(token_url, data=data) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Failed to get admin token: {await resp.text()}")
                body = await resp.json()
                self._access_token = body["access_token"]
                return self._access_token

    async def _admin_request(self, method: str, path: str, json=None) -> dict:
        token = await self._get_admin_token()
        url = f"{self.keycloak_url}/admin/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {token}"}
        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, json=json, headers=headers) as resp:
                if resp.status not in (200, 201, 204):
                    text = await resp.text()
                    raise RuntimeError(f"Keycloak admin request failed: {resp.status} {text}")
                if resp.status == 204:
                    return {}
                return await resp.json()

    async def create_platform_realm(self, realm_name: str) -> str:
        """Create the platform realm if it doesn't exist."""
        # Check if realm exists
        realms = await self._admin_request("GET", "realms")
        if any(r["realm"] == realm_name for r in realms):
            return realm_name
        # Create realm
        payload = {
            "id": realm_name,
            "realm": realm_name,
            "enabled": True,
            "displayName": "Platform",
            "loginWithEmailAllowed": True,
            "duplicateEmailsAllowed": False,
            "resetPasswordAllowed": True,
            "editUsernameAllowed": False,
            "sslRequired": "all",
            "accessTokenLifespan": 300,
            "ssoSessionMaxLifespan": 36000,
        }
        await self._admin_request("POST", "realms", json=payload)
        return realm_name

    async def create_admin_user(self, realm: str, username: str, email: str, password: str) -> str:
        """Create an admin user in the platform realm and assign admin role."""
        # Create user
        payload = {
            "username": username,
            "email": email,
            "enabled": True,
            "emailVerified": True,
            "credentials": [{"type": "password", "value": password, "temporary": False}]
        }
        await self._admin_request("POST", f"realms/{realm}/users", json=payload)
        # Get user ID
        users = await self._admin_request("GET", f"realms/{realm}/users?username={username}")
        user_id = users[0]["id"]
        # Assign admin role
        roles = await self._admin_request("GET", f"realms/{realm}/roles")
        admin_role = next((r for r in roles if r["name"] == "admin"), None)
        if admin_role:
            await self._admin_request("POST", f"realms/{realm}/users/{user_id}/role-mappings/realm", json=[admin_role])
        return user_id