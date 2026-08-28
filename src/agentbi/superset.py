"""Small, bounded client for synchronizing governed Superset metadata."""

from __future__ import annotations

from typing import Any

import httpx


class SupersetApiError(RuntimeError):
    """A safe upstream error that does not expose credentials or response bodies."""


class SupersetClient:
    def __init__(
        self,
        base_url: str,
        username: str | None,
        password: str | None,
        timeout_seconds: int,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout_seconds = min(timeout_seconds, 20)

    async def list_dashboards(self) -> list[dict[str, object]]:
        if not self.username or not self.password:
            raise SupersetApiError("未配置 Superset 同步账号")
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                login = await client.post(
                    "/api/v1/security/login",
                    json={
                        "username": self.username,
                        "password": self.password,
                        "provider": "db",
                        "refresh": True,
                    },
                )
                login.raise_for_status()
                token = login.json().get("access_token")
                if not token:
                    raise SupersetApiError("Superset 登录响应无效")
                headers = {"Authorization": f"Bearer {token}"}
                response = await client.get(
                    "/api/v1/dashboard/",
                    params={"q": "(page:0,page_size:100)"},
                    headers=headers,
                )
                response.raise_for_status()
                results = response.json().get("result", [])[:200]
                dashboards = []
                for item in results:
                    dashboard_id = int(item["id"])
                    detail_response = await client.get(
                        f"/api/v1/dashboard/{dashboard_id}", headers=headers
                    )
                    detail_response.raise_for_status()
                    detail: dict[str, Any] = detail_response.json().get("result", {})
                    path = f"/superset/dashboard/{dashboard_id}/"
                    dashboards.append(
                        {
                            "superset_id": dashboard_id,
                            "title": str(detail.get("dashboard_title") or item.get("dashboard_title") or f"Dashboard {dashboard_id}"),
                            "slug": detail.get("slug") or item.get("slug"),
                            "url_path": path,
                            "chart_count": len(detail.get("charts") or []),
                            "published": bool(detail.get("published", item.get("published", False))),
                        }
                    )
                return dashboards
        except SupersetApiError:
            raise
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise SupersetApiError("Superset 元数据同步失败，请检查服务和同步账号") from exc

    async def list_data_assets(self) -> dict[str, list[dict[str, object]]]:
        """Return real database and dataset metadata without connection secrets."""

        if not self.username or not self.password:
            raise SupersetApiError("未配置 Superset 同步账号")
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                login = await client.post(
                    "/api/v1/security/login",
                    json={
                        "username": self.username,
                        "password": self.password,
                        "provider": "db",
                        "refresh": True,
                    },
                )
                login.raise_for_status()
                token = login.json().get("access_token")
                if not token:
                    raise SupersetApiError("Superset 登录响应无效")
                headers = {"Authorization": f"Bearer {token}"}
                database_response, dataset_response = await self._get_data_assets(
                    client, headers
                )
                databases = [
                    {
                        "superset_id": int(item["id"]),
                        "name": str(item.get("database_name") or f"Database {item['id']}"),
                        "backend": str(item.get("backend") or "unknown"),
                        "expose_in_sqllab": bool(item.get("expose_in_sqllab", False)),
                        "allow_file_upload": bool(item.get("allow_file_upload", False)),
                        "dataset_count": 0,
                    }
                    for item in database_response.json().get("result", [])[:200]
                ]
                database_by_id = {item["superset_id"]: item for item in databases}
                datasets = []
                for item in dataset_response.json().get("result", [])[:500]:
                    database = item.get("database") or {}
                    database_id = int(database.get("id", 0))
                    if database_id in database_by_id:
                        database_by_id[database_id]["dataset_count"] += 1
                    datasets.append(
                        {
                            "superset_id": int(item["id"]),
                            "name": str(item.get("table_name") or f"Dataset {item['id']}"),
                            "database_id": database_id,
                            "database_name": str(database.get("database_name") or "未知数据库"),
                            "schema": str(item.get("schema") or "—"),
                            "kind": str(item.get("kind") or "physical"),
                            "description": str(item.get("description") or ""),
                            "explore_url": str(item.get("explore_url") or ""),
                        }
                    )
                return {"databases": databases, "datasets": datasets}
        except SupersetApiError:
            raise
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise SupersetApiError("Superset 数据源读取失败，请检查服务和同步账号") from exc

    @staticmethod
    async def _get_data_assets(
        client: httpx.AsyncClient, headers: dict[str, str]
    ) -> tuple[httpx.Response, httpx.Response]:
        database_response = await client.get(
            "/api/v1/database/", params={"q": "(page:0,page_size:100)"}, headers=headers
        )
        dataset_response = await client.get(
            "/api/v1/dataset/", params={"q": "(page:0,page_size:500)"}, headers=headers
        )
        database_response.raise_for_status()
        dataset_response.raise_for_status()
        return database_response, dataset_response
