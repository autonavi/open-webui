import requests
import logging
from typing import Iterator, Optional
from bs4 import BeautifulSoup

from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document

log = logging.getLogger(__name__)


class ConfluenceLoader(BaseLoader):
    """
    Confluence 知识库 Loader。
    支持通过 Space Key 同步整个空间，或通过 Page ID 同步单个页面及其子页面。
    支持 Basic Auth (用户名+密码) 和 Bearer Token (PAT) 两种认证方式。
    """
    def __init__(
        self,
        confluence_url: str,
        space_key: str,
        username: Optional[str] = None,
        api_token: Optional[str] = None,
        continue_on_failure: bool = True,
        **kwargs,
    ) -> None:
        """
        :param confluence_url: Confluence 基础 URL (例如 http://cwiki.example.com:8099)
        :param space_key: Space Key (如 DEV) 或 Page ID (如 24805383)
        :param username: 用户名 (留空则使用 Bearer Token)
        :param api_token: 密码 / API Token / PAT
        :param continue_on_failure: 出现错误时是否继续
        """
        self.confluence_url = confluence_url.rstrip("/")
        self.space_key = space_key.strip()
        self.username = username
        self.api_token = api_token
        self.continue_on_failure = continue_on_failure

    def _request(self, url: str, params: dict = None) -> requests.Response:
        """发送请求，自动选择认证方式。"""
        headers = {"Accept": "application/json"}
        auth = None

        if self.username and self.username.strip():
            auth = (self.username, self.api_token)
        elif self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"

        return requests.get(url, params=params, auth=auth, headers=headers, timeout=30)

    def _parse_page(self, result: dict) -> Document:
        """将 API 返回的页面数据解析为 Document。"""
        title = result.get("title", "")
        content_id = result.get("id", "")

        body_html = result.get("body", {}).get("storage", {}).get("value", "")
        soup = BeautifulSoup(body_html, "html.parser")
        text_content = soup.get_text(separator="\n", strip=True)

        page_content = f"Title: {title}\n\n{text_content}"
        page_url = f"{self.confluence_url}/pages/viewpage.action?pageId={content_id}"

        return Document(
            page_content=page_content,
            metadata={
                "source": page_url,
                "title": title,
                "id": content_id,
                "space_key": self.space_key,
            },
        )

    def _get_api_url(self, path: str) -> str:
        """
        拼接 API URL。直接使用 /rest/api 路径（适用于大多数自托管 Confluence Server）。
        """
        return f"{self.confluence_url}/rest/api{path}"

    def lazy_load(self) -> Iterator[Document]:
        if self.space_key.isdigit():
            log.info(f"Input '{self.space_key}' is numeric, loading as Page ID")
            yield from self._load_page(self.space_key)
        else:
            log.info(f"Loading Confluence space: {self.space_key}")
            yield from self._load_space()

    def _load_page(self, page_id: str) -> Iterator[Document]:
        """通过 Page ID 加载单个页面及其子页面。"""
        # 加载主页面
        try:
            url = self._get_api_url(f"/content/{page_id}")
            resp = self._request(url, params={"expand": "body.storage,version"})
            resp.raise_for_status()
            yield self._parse_page(resp.json())
        except Exception as e:
            error_msg = f"Error fetching page {page_id}: {e}"
            if self.continue_on_failure:
                log.error(error_msg)
            else:
                raise Exception(error_msg) from e
            return

        # 加载子页面
        children_url = self._get_api_url(f"/content/{page_id}/child/page")
        params = {"expand": "body.storage,version", "limit": 50}

        while children_url:
            try:
                resp = self._request(children_url, params=params)
                resp.raise_for_status()
                data = resp.json()

                for result in data.get("results", []):
                    yield self._parse_page(result)

                next_link = data.get("_links", {}).get("next")
                if next_link:
                    children_url = f"{self.confluence_url}{next_link}"
                    params = None
                else:
                    children_url = None
            except Exception as e:
                error_msg = f"Error loading child pages of {page_id}: {e}"
                if self.continue_on_failure:
                    log.error(error_msg)
                else:
                    raise Exception(error_msg) from e
                break

    def _load_space(self) -> Iterator[Document]:
        """通过 Space Key 加载整个空间的所有页面。"""
        url = self._get_api_url("/content")
        params = {
            "spaceKey": self.space_key,
            "expand": "body.storage,version",
            "limit": 50,
        }

        while url:
            try:
                resp = self._request(url, params=params)
                resp.raise_for_status()
                data = resp.json()

                for result in data.get("results", []):
                    yield self._parse_page(result)

                next_link = data.get("_links", {}).get("next")
                if next_link:
                    url = f"{self.confluence_url}{next_link}"
                    params = None
                else:
                    url = None
            except Exception as e:
                error_msg = f"Error loading Confluence space '{self.space_key}': {e}"
                if self.continue_on_failure:
                    log.error(error_msg)
                else:
                    raise Exception(error_msg) from e
                break
