import requests
import logging
from typing import Iterator
from bs4 import BeautifulSoup

from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document

log = logging.getLogger(__name__)


class ConfluenceLoader(BaseLoader):
    """
    一个专门用于将 Confluence 特定工作区的数据同步到知识库的 Loader
    """
    def __init__(
        self,
        confluence_url: str,
        space_key: str,
        username: str,
        api_token: str,
        continue_on_failure: bool = True,
        **kwargs,
    ) -> None:
        """
        初始化 ConfluenceLoader。
        
        :param confluence_url: Confluence 实例基础 URL (例如 https://your-domain.atlassian.net)
        :param space_key: 要同步的具体工作区 (Space) 的 KEY
        :param username: Confluence 账号用户名/邮箱
        :param api_token: Confluence API Token 或 密码
        :param continue_on_failure: 出现错误时是否继续执行
        """
        self.confluence_url = confluence_url.rstrip("/")
        self.space_key = space_key
        self.username = username
        self.api_token = api_token
        self.continue_on_failure = continue_on_failure

    def lazy_load(self) -> Iterator[Document]:
        url = f"{self.confluence_url}/wiki/rest/api/content"
        
        # Confluence API 参数：指定抓取空间、获取页面主体的存储格式(包含HTML代码)
        params = {
            "spaceKey": self.space_key,
            "expand": "body.storage,version",
            "limit": 50, # 每次请求获取50篇文档
        }
        
        while url:
            try:
                response = requests.get(
                    url,
                    params=params,
                    auth=(self.username, self.api_token),
                    headers={"Accept": "application/json"}
                )
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
                    # 注意：Confluence Cloud 通常使用 /wiki/spaces/{space_key}/pages/{page_id} 的格式
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
                
                # 检查并处理分页：获取下一页的数据链接
                next_path = data.get("_links", {}).get("next")
                if next_path:
                    # Confluence 会在 next 链接中自动包含需要的分页标识和上述的 params
                    # 因此后续请求可以直接调用新的完整 URL，清空 params
                    url = f"{self.confluence_url}{next_path}"
                    params = None 
                else:
                    url = None
                    
            except Exception as e:
                error_msg = f"Error extracting content from Confluence Workspace '{self.space_key}': {e}"
                if self.continue_on_failure:
                    log.error(error_msg)
                    break # 如果出错则中断当前空间的拉取，可以抛出只为了某几篇报错不影响整体
                else:
                    raise Exception(error_msg) from e
