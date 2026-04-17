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

        :param confluence_url: Confluence 实例基础 URL (例如 https://your-domain.atlassian.net 或 http://host:port)
        :param space_key: 要同步的具体工作区 (Space) 的 KEY
        :param username: Confluence 账号用户名/邮箱 (留空则使用 Bearer Token 认证)
        :param api_token: Confluence API Token、密码 或 Personal Access Token (PAT)
        :param continue_on_failure: 出现错误时是否继续执行
        """
        self.confluence_url = confluence_url.rstrip("/")
        self.space_key = space_key
        self.username = username
        self.api_token = api_token
        self.continue_on_failure = continue_on_failure

    def _get_auth_headers(self) -> dict:
        """根据是否提供用户名，决定使用 Basic Auth 还是 Bearer Token。"""
        headers = {"Accept": "application/json"}

        if self.username and self.username.strip():
            # Basic Auth: 用户名+密码/API Token (Confluence Cloud 或 Server)
            return headers
        else:
            # Bearer Token: PAT 方式 (Confluence Server 7.9+)
            if self.api_token:
                headers["Authorization"] = f"Bearer {self.api_token}"
            return headers

    def _get_auth_tuple(self):
        """如果提供了用户名，返回 Basic Auth tuple；否则返回 None（使用 Bearer）。"""
        if self.username and self.username.strip():
            return (self.username, self.api_token)
        return None

    def _try_api_request(self, url: str, params: dict = None) -> requests.Response:
        """
        尝试请求 Confluence API。
        若 /wiki/rest/api 路径返回 404，则回退到 /rest/api 路径（某些自托管部署不含 /wiki/ 前缀）。
        """
        auth = self._get_auth_tuple()
        headers = self._get_auth_headers()

        response = requests.get(url, params=params, auth=auth, headers=headers)

        # 如果返回404且路径包含/wiki/，尝试不带/wiki/的路径
        if response.status_code == 404 and '/wiki/rest/api/' in url:
            alt_url = url.replace('/wiki/rest/api/', '/rest/api/')
            log.info(f"Got 404 for {url}, retrying without /wiki/ prefix: {alt_url}")
            response = requests.get(alt_url, params=params, auth=auth, headers=headers)
            if response.ok:
                # 记住正确的base路径，后续分页请求也用这个
                self.confluence_url = self.confluence_url.replace('/wiki', '')
                self._use_wiki_prefix = False

        return response

    def lazy_load(self) -> Iterator[Document]:
        url = f"{self.confluence_url}/wiki/rest/api/content"

        # Confluence API 参数：指定抓取空间、获取页面主体的存储格式(包含HTML代码)
        params = {
            "spaceKey": self.space_key,
            "expand": "body.storage,version",
            "limit": 50, # 每次请求获取50篇文档
        }

        page_count = 0

        while url:
            try:
                if page_count == 0:
                    response = self._try_api_request(url, params)
                else:
                    auth = self._get_auth_tuple()
                    headers = self._get_auth_headers()
                    response = requests.get(url, params=params, auth=auth, headers=headers)

                response.raise_for_status()
                data = response.json()

                results = data.get("results", [])
                for result in results:
                    title = result.get("title", "")
                    content_id = result.get("id", "")

                    # 提取页面的 HTML 正文内容
                    body_html = result.get("body", {}).get("storage", {}).get("value", "")

                    # 使用 BeautifulSoup 解析并清理 HTML，将其转换为纯文本
                    soup = BeautifulSoup(body_html, "html.parser")
                    text_content = soup.get_text(separator="\n", strip=True)

                    # 增加标题等基本结构标识
                    page_content = f"Title: {title}\n\n{text_content}"

                    # 构建元数据，附加源地址便于后续知识库溯源引用
                    page_url = f"{self.confluence_url}/wiki/spaces/{self.space_key}/pages/{content_id}"

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

                page_count += 1

                # 检查并处理分页：获取下一页的数据链接
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
