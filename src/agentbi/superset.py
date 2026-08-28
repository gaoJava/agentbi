"""Small, bounded client for synchronizing governed Superset metadata."""

from __future__ import annotations

from typing import Any, ClassVar
from urllib.parse import urlsplit

import httpx


class SupersetApiError(RuntimeError):
    """A safe upstream error that does not expose credentials or response bodies."""


class SupersetClient:
    _ALLOWED_DATABASE_SCHEMES: ClassVar[set[str]] = {
        "postgresql", "postgresql+psycopg2", "mysql", "mysql+pymysql",
        "clickhouse", "clickhousedb", "doris", "trino", "mssql", "oracle",
    }
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
                available_response = await client.get("/api/v1/database/available/", headers=headers)
                available_response.raise_for_status()
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
                available_engines = [
                    {
                        "engine": str(item.get("engine") or ""),
                        "name": str(item.get("name") or item.get("engine") or ""),
                        "drivers": list(item.get("available_drivers") or []),
                        "placeholder": str(item.get("sqlalchemy_uri_placeholder") or ""),
                    }
                    for item in available_response.json().get("databases", [])
                ]
                return {"databases": databases, "datasets": datasets, "available_engines": available_engines}
        except SupersetApiError:
            raise
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise SupersetApiError("Superset 数据源读取失败，请检查服务和同步账号") from exc

    async def test_database_connection(self, database_name: str, sqlalchemy_uri: str) -> None:
        payload = self._database_payload(database_name, sqlalchemy_uri)
        response = await self._authorized_request(
            "POST", "/api/v1/database/test_connection/", json=payload
        )
        if response.status_code >= 400:
            raise SupersetApiError("连接测试失败，请检查地址、驱动和账号密码")

    async def create_database(
        self, database_name: str, sqlalchemy_uri: str, expose_in_sqllab: bool
    ) -> dict[str, object]:
        payload = self._database_payload(database_name, sqlalchemy_uri)
        payload["expose_in_sqllab"] = expose_in_sqllab
        response = await self._authorized_request("POST", "/api/v1/database/", json=payload)
        if response.status_code >= 400:
            raise SupersetApiError("数据库连接创建失败，名称可能重复或连接参数无效")
        body = response.json()
        return {"superset_id": body.get("id"), "name": database_name}

    async def update_database(
        self, database_id: int, database_name: str, sqlalchemy_uri: str,
        expose_in_sqllab: bool,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "database_name": database_name.strip(),
            "expose_in_sqllab": expose_in_sqllab,
        }
        if sqlalchemy_uri.strip():
            self._database_payload(database_name, sqlalchemy_uri)
            payload["sqlalchemy_uri"] = sqlalchemy_uri.strip()
        response = await self._authorized_request(
            "PUT", f"/api/v1/database/{database_id}", json=payload
        )
        if response.status_code >= 400:
            raise SupersetApiError("数据库连接修改失败，请检查名称和新连接串")
        return {"superset_id": database_id, "name": database_name.strip()}

    async def create_dataset(
        self, database_id: int, schema_name: str, table_name: str
    ) -> dict[str, object]:
        response = await self._authorized_request(
            "POST", "/api/v1/dataset/",
            json={"database": database_id, "schema": schema_name.strip() or None,
                  "table_name": table_name.strip()},
        )
        if response.status_code >= 400:
            raise SupersetApiError("Dataset 创建失败，请检查数据库、Schema、表名或重复配置")
        body = response.json()
        return {"superset_id": body.get("id"), "name": table_name.strip()}

    async def update_dataset(self, dataset_id: int, description: str) -> dict[str, object]:
        response = await self._authorized_request(
            "PUT", f"/api/v1/dataset/{dataset_id}", json={"description": description.strip()}
        )
        if response.status_code >= 400:
            raise SupersetApiError("Dataset 修改失败")
        return {"superset_id": dataset_id, "description": description.strip()}

    async def get_dataset(self, dataset_id: int) -> dict[str, object]:
        response = await self._authorized_request("GET", f"/api/v1/dataset/{dataset_id}")
        if response.status_code >= 400:
            raise SupersetApiError("Dataset 不存在或无权访问")
        item = response.json().get("result", {})
        return {
            "superset_id": dataset_id,
            "name": str(item.get("table_name") or item.get("datasource_name") or dataset_id),
            "schema": str(item.get("schema") or "—"),
            "database_name": str((item.get("database") or {}).get("database_name") or "—"),
            "columns": [
                {"name": str(column.get("column_name") or ""),
                 "type": str(column.get("type") or "未知"),
                 "is_time": bool(column.get("is_dttm", False)),
                 "filterable": bool(column.get("filterable", False))}
                for column in (item.get("columns") or [])[:500]
            ],
            "metrics": [str(metric.get("metric_name") or "") for metric in (item.get("metrics") or [])[:200]],
        }

    async def delete_dataset(self, dataset_id: int) -> None:
        related = await self._authorized_request("GET", f"/api/v1/dataset/{dataset_id}/related_objects")
        if related.status_code >= 400:
            raise SupersetApiError("无法检查 Dataset 引用关系")
        chart_count = int((related.json().get("charts") or {}).get("count", 0))
        if chart_count:
            raise SupersetApiError(f"Dataset 正被 {chart_count} 个图表引用，不能删除")
        response = await self._authorized_request("DELETE", f"/api/v1/dataset/{dataset_id}")
        if response.status_code >= 400:
            raise SupersetApiError("Dataset 删除失败")

    async def delete_database(self, database_id: int) -> None:
        related = await self._authorized_request("GET", f"/api/v1/database/{database_id}/related_objects/")
        if related.status_code >= 400:
            raise SupersetApiError("无法检查数据库连接引用关系")
        body = related.json()
        counts = {name: int((body.get(name) or {}).get("count", 0))
                  for name in ("charts", "dashboards", "sqllab_tab_states")}
        if any(counts.values()):
            raise SupersetApiError(
                f"数据库仍关联 {counts['charts']} 个图表和 {counts['dashboards']} 个仪表盘，不能删除"
            )
        response = await self._authorized_request("DELETE", f"/api/v1/database/{database_id}")
        if response.status_code >= 400:
            raise SupersetApiError("数据库连接删除失败")

    def _database_payload(self, database_name: str, sqlalchemy_uri: str) -> dict[str, object]:
        parsed = urlsplit(sqlalchemy_uri.strip())
        if parsed.scheme.lower() not in self._ALLOWED_DATABASE_SCHEMES:
            raise SupersetApiError("不支持该数据库驱动，请使用 PostgreSQL、MySQL、Doris 等受支持连接")
        if not parsed.hostname:
            raise SupersetApiError("数据库连接地址无效")
        return {
            "database_name": database_name.strip(),
            "sqlalchemy_uri": sqlalchemy_uri.strip(),
            "impersonate_user": False,
            "server_cert": None,
            "configuration_method": "sqlalchemy_form",
        }

    async def _authorized_request(
        self, method: str, path: str, **kwargs: object
    ) -> httpx.Response:
        if not self.username or not self.password:
            raise SupersetApiError("未配置 Superset 同步账号")
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=self.timeout_seconds,
                follow_redirects=False, trust_env=False,
            ) as client:
                login = await client.post(
                    "/api/v1/security/login",
                    json={"username": self.username, "password": self.password,
                          "provider": "db", "refresh": True},
                )
                login.raise_for_status()
                token = login.json().get("access_token")
                if not token:
                    raise SupersetApiError("Superset 登录响应无效")
                headers = dict(kwargs.pop("headers", {}) or {})
                headers["Authorization"] = f"Bearer {token}"
                return await client.request(method, path, headers=headers, **kwargs)
        except SupersetApiError:
            raise
        except httpx.HTTPError as exc:
            raise SupersetApiError("Superset 服务当前不可访问") from exc

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
