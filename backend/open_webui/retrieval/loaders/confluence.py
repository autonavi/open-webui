import requests
import logging
from typing import Iterator, Optional
from bs4 import BeautifulSoup

from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document

log = logging.getLogger(__name__)


class ConfluenceLoader(BaseLoader):
    """
    一个专门用于将 Confluence 特定工作区的数据同步到知识库的 Loader。
    支持 Confluence Cloud (Basic Auth) 和 Confluence Server (Basic Auth 或 PAT Bearer Token)。
    支持通过 Space Key 同步整个空间，或通过 Page ID 同步单个页面。
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
        初始化 ConfluenceLoader。

        :param confluence_url: Confluence 实例基础 URL (例如 http://cwiki.example.com:8099)
        :param space_key: Space Key (如 DEV, TECH) 或 Page ID (如 24805383)
        :param username: Confluence 账号用户名 (留空则使用 Bearer Token 认证)
        :param api_token: Confluence 密码、API Token 或 Personal Access Token (PAT)
        :param continue_on_failure: 出现错误时是否继续执行
        """
        self.confluence_url = confluence_url.rstrip("/")
        self.space_key = space_key
        self.username = username
        self.api_token = api_token
        self.continue_on_failure = continue_on_failure
        self._api_base = None  # 缓存找到的正确 API 基础路径

    def _get_auth(self):
        """返回 Basic Auth tuple 或 None (使用 Bearer Token)。"""
        if self.username and self.username.strip():
            return (self.username, self.api_token)
        return None

    def _get_headers(self) -> dict:
        """构造请求头。有用户名用 Basic Auth，无用户名用 Bearer Token。"""
        headers = {"Accept": "application/json"}
        if not (self.username and self.username.strip()):
            if self.api_token:
                headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    def _discover_api_base(self) -> str:
        """
        自动探测 Confluence REST API 的基础路径。
        依次尝试: /wiki/rest/api, /rest/api, /confluence/rest/api
        """
        if self._api_base:
            return self._api_base

        candidates = [
            f"{self.confluence_url}/rest/api",
            f"{self.confluence_url}/wiki/rest/api",
            f"{self.confluence_url}/confluence/rest/api",
        ]

        auth = self._get_auth()
        headers = self._get_headers()

        for base in candidates:
            try:
                # 用一个轻量请求测试路径是否可用
                test_url = f"{base}/space"
                log.info(f"Trying Confluence API base: {test_url}")
                resp = requests.get(
                    test_url, params={"limit": 1},
                    auth=auth, headers=headers, timeout=15
                )
                if resp.status_code == 200:
                    self._api_base = base
                    log.info(f"Found Confluence API at: {base}")
                    return base
                else:
                    log.info(f"  -> {resp.status_code}")
            except requests.RequestException as e:
                log.info(f"  -> Connection error: {e}")
                continue

        # 默认回退到不带 /wiki/ 的路径
        self._api_base = f"{self.confluence_url}/rest/api"
        log.warning(f"Could not auto-detect API base, falling back to: {self._api_base}")
        return self._api_base

    def _is_page_id(self) -> bool:
        """判断 space_key 是否为数字形式的 Page ID。"""
        return self.space_key.strip().isdigit()

    def _load_by_page_id(self) -> Iterator[Document]:
        """通过 Page ID 加载单个页面及其子页面。"""
        api_base = self._discover_api_base()
        auth = self._get_auth()
        headers = self._get_headers()
        page_id = self.space_key.strip()

        # 获取指定页面
        yield from self._fetch_page(api_base, page_id, auth, headers)

        # 获取子页面
        children_url = f"{api_base}/content/{page_id}/child/page"
        params = {"expand": "body.storage,version", "limit": 50}

        while children_url:
            try:
                resp = requests.get(children_url, params=params, auth=auth, headers=headers, timeout=30)
                resp.raise_for_status()
                data = resp.json()

                for result in data.get("results", []):
                    yield from self._parse_result(result)

                next_path = data.get("_links", {}).get("next")
                if next_path:
                    children_url = f"{self.confluence_url}{next_path}"
                    params = None
                else:
                    children_url = None
            except Exception as e:
                error_msg = f"Error loading child pages of {page_id}: {e}"
                if self.continue_on_failure:
                    log.error(error_msg)
                    break
                else:
                    raise Exception(error_msg) from e

    def _fetch_page(self, api_base: str, page_id: str, auth, headers) -> Iterator[Document]:
        """获取单个页面并生成 Document。"""
        try:
            url = f"{api_base}/content/{page_id}"
            params = {"expand": "body.storage,version"}
            resp = requests.get(url, params=params, auth=auth, headers=headers, timeout=30)
            resp.raise_for_status()
            result = resp.json()
            yield from self._parse_result(result)
        except Exception as e:
            error_msg = f"Error fetching page {page_id}: {e}"
            if self.continue_on_failure:
                log.error(error_msg)
            else:
                raise Exception(error_msg) from e

    def _load_by_space_key(self) -> Iterator[Document]:
        """通过 Space Key 加载整个空间的所有页面。"""
        api_base = self._discover_api_base()
        auth = self._get_auth()
        headers = self._get_headers()

        url = f"{api_base}/content"
        params = {
            "spaceKey": self.space_key,
            "expand": "body.storage,version",
            "limit": 50,
        }

        while url:
            try:
                resp = requests.get(url, params=params, auth=auth, headers=headers, timeout=30)
                resp.raise_for_status()
                data = resp.json()

                for result in data.get("results", []):
                    yield from self._parse_result(result)

                next_path = data.get("_links", {}).get("next")
                if next_path:
                    url = f"{self.confluence_url}{next_path}"
                    params = None
                else:
                    url = None

            except Exception as e:
                error_msg = f"Error extracting content from Confluence Workspace '{self.space_key}': {e}"
                if self.continue_on_failure:
                    log.error(error_msg)
                    break
                else:
                    raise Exception(error_msg) from e

    def _parse_result(self, result: dict) -> Iterator[Document]:
        """将 Confluence API 返回的单条结果解析为 Document。"""
        title = result.get("title", "")
        content_id = result.get("id", "")

        body_html = result.get("body", {}).get("storage", {}).get("value", "")
        soup = BeautifulSoup(body_html, "html.parser")
        text_content = soup.get_text(separator="\n", strip=True)

        page_content = f"Title: {title}\n\n{text_content}"

        page_url = f"{self.confluence_url}/pages/viewpage.action?pageId={content_id}"

        metadata = {
            "source": page_url,
            "title": title,
            "id": content_id,
            "space_key": self.space_key,
        }

        yield Document(
            page_content=page_content,
            metadata=metadata,
        )

    def lazy_load(self) -> Iterator[Document]:
        if self._is_page_id():
            log.info(f"Detected numeric input '{self.space_key}', loading as Page ID")
            yield from self._load_by_page_id()
        else:
            log.info(f"Loading Confluence space: {self.space_key}")
            yield from self._load_by_space_key()
