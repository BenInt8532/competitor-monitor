"""Запросы к тому же FastAPI, что и веб-интерфейс."""

from typing import Optional

import requests

import server


class ApiClient:
    """
    Адрес API не фиксируем при импорте: объект `api` создаётся раньше, чем
    server.start() выберет порт (8000 может быть занят чужим процессом).
    Поэтому base — вычисляемое свойство, а не строка из конструктора.
    """

    def __init__(self, base: Optional[str] = None):
        self._base = base.rstrip("/") if base else None

    @property
    def base(self) -> str:
        return self._base or server.base_url()

    @base.setter
    def base(self, value: Optional[str]) -> None:
        self._base = value.rstrip("/") if value else None

    def _url(self, path: str) -> str:
        return f"{self.base}{path}"

    def _parse(self, response: requests.Response) -> dict:
        try:
            data = response.json()
        except Exception:
            return {
                "success": False,
                "error": f"HTTP {response.status_code}: {response.text[:400]}",
            }
        if not isinstance(data, dict):
            return {"success": True, "data": data}
        if "error" not in data and "detail" in data:
            data["error"] = str(data["detail"])
        if not response.ok:
            data.setdefault("success", False)
            data.setdefault("error", f"HTTP {response.status_code}")
        return data

    def _request(self, method: str, path: str, timeout: int, **kwargs) -> dict:
        try:
            response = requests.request(
                method, self._url(path), timeout=timeout, **kwargs
            )
            return self._parse(response)
        except requests.exceptions.ConnectionError:
            return {
                "success": False,
                "error": "Нет связи с сервером. Подожди пару секунд и попробуй снова.",
            }
        except requests.exceptions.Timeout:
            return {
                "success": False,
                "error": "Сервер думает слишком долго. Часто так бывает на разборе сайта.",
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def health(self) -> bool:
        try:
            response = requests.get(self._url("/health"), timeout=2)
            return response.ok and response.json().get("status") == "ok"
        except Exception:
            return False

    def config(self) -> dict:
        return self._request("GET", "/config", timeout=10)

    def parse_urls(self, raw: str) -> dict:
        return self._request("POST", "/urls/parse", timeout=20, json={"raw": raw})

    def analyze_url(self, url: str, include_reviews: bool = True) -> dict:
        return self._request(
            "POST",
            "/analyze/url",
            timeout=300,
            json={"url": url, "include_reviews": include_reviews},
        )

    def analyze_text(
        self,
        text: str,
        competitor_name: Optional[str] = None,
        exchanger_rate: Optional[float] = None,
    ) -> dict:
        return self._request(
            "POST",
            "/analyze/text",
            timeout=180,
            json={
                "text": text,
                "competitor_name": competitor_name or None,
                "exchanger_rate": exchanger_rate,
            },
        )

    def analyze_file(self, path: str) -> dict:
        try:
            with open(path, "rb") as handle:
                name = path.replace("\\", "/").split("/")[-1]
                response = requests.post(
                    self._url("/analyze/file"),
                    files={"file": (name, handle)},
                    timeout=180,
                )
            return self._parse(response)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def analyze_reviews(self, url: str, extra_urls: Optional[list] = None) -> dict:
        return self._request(
            "POST",
            "/analyze/reviews",
            timeout=180,
            json={"url": url, "extra_urls": extra_urls or []},
        )

    def analyze_batch(self) -> dict:
        return self._request("POST", "/analyze/batch", timeout=600, json={})

    def summary(self) -> dict:
        return self._request("GET", "/summary", timeout=30)

    def refresh_rates(self, amount: int = 1000) -> dict:
        return self._request(
            "POST",
            "/summary/rates/refresh",
            timeout=180,
            params={"amount": amount},
        )

    def export_csv(self) -> tuple[bytes, Optional[str]]:
        try:
            response = requests.get(self._url("/summary/export"), timeout=30)
            response.raise_for_status()
            return response.content, None
        except Exception as exc:
            return b"", str(exc)

    def history(self) -> dict:
        return self._request("GET", "/history", timeout=20)

    def clear_history(self) -> dict:
        return self._request("DELETE", "/history", timeout=20)

    def remove_competitor(self, name: str) -> dict:
        return self._request(
            "POST",
            "/competitors/remove",
            timeout=20,
            json={"competitor_name": name},
        )


api = ApiClient()
