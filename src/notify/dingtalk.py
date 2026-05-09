"""钉钉自定义机器人 Webhook 发送器。

支持类型：
- markdown （汇总）
- actionCard （重点单发）
- text （回退）
- link / feedCard （扩展可用）

文档参考: https://open.dingtalk.com/document/development/robot-message-type
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
import urllib.parse
from typing import Dict, List, Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)


class DingTalkClient:
    def __init__(
        self,
        webhook: str,
        secret: str = "",
        at_mobiles: Optional[List[str]] = None,
        at_all: bool = False,
        timeout: int = 10,
        max_retry: int = 3,
    ):
        self.webhook = webhook
        self.secret = secret
        self.at_mobiles = at_mobiles or []
        self.at_all = at_all
        self.timeout = timeout
        self.max_retry = max_retry

    # ----- 加签 -----
    def _signed_url(self) -> str:
        if not self.secret:
            return self.webhook
        ts = str(round(time.time() * 1000))
        sign_str = f"{ts}\n{self.secret}"
        h = hmac.new(self.secret.encode("utf-8"), sign_str.encode("utf-8"), digestmod=hashlib.sha256)
        sign = urllib.parse.quote_plus(base64.b64encode(h.digest()))
        sep = "&" if "?" in self.webhook else "?"
        return f"{self.webhook}{sep}timestamp={ts}&sign={sign}"

    def _at_payload(self) -> Dict:
        return {
            "atMobiles": self.at_mobiles,
            "isAtAll": self.at_all,
        }

    # ----- 公开发送方法 -----
    def send_text(self, content: str) -> Dict:
        payload = {
            "msgtype": "text",
            "text": {"content": content},
            "at": self._at_payload(),
        }
        return self._post(payload)

    def send_markdown(self, title: str, text: str) -> Dict:
        payload = {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": text},
            "at": self._at_payload(),
        }
        return self._post(payload)

    def send_link(self, title: str, text: str, message_url: str, pic_url: str = "") -> Dict:
        payload = {
            "msgtype": "link",
            "link": {
                "title": title,
                "text": text,
                "messageUrl": message_url,
                "picUrl": pic_url,
            },
        }
        return self._post(payload)

    def send_action_card(
        self,
        title: str,
        text: str,
        single_title: str,
        single_url: str,
    ) -> Dict:
        payload = {
            "msgtype": "actionCard",
            "actionCard": {
                "title": title,
                "text": text,
                "singleTitle": single_title,
                "singleURL": single_url,
            },
        }
        return self._post(payload)

    def send_action_card_multi(self, title: str, text: str, btns: List[Dict], orientation: str = "0") -> Dict:
        payload = {
            "msgtype": "actionCard",
            "actionCard": {
                "title": title,
                "text": text,
                "btnOrientation": orientation,
                "btns": btns,
            },
        }
        return self._post(payload)

    def send_feed_card(self, links: List[Dict]) -> Dict:
        payload = {"msgtype": "feedCard", "feedCard": {"links": links}}
        return self._post(payload)

    # ----- 底层 -----
    def _post(self, payload: Dict) -> Dict:
        @retry(
            stop=stop_after_attempt(self.max_retry),
            wait=wait_exponential(multiplier=1, min=1, max=6),
            retry=retry_if_exception_type((requests.RequestException,)),
            reraise=True,
        )
        def _do() -> Dict:
            url = self._signed_url()
            r = requests.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=self.timeout,
            )
            r.raise_for_status()
            data = r.json()
            if data.get("errcode", 0) != 0:
                raise RuntimeError(f"dingtalk error: {data}")
            return data

        try:
            return _do()
        except Exception as e:  # noqa: BLE001
            logger.error("钉钉发送失败: %s payload=%s", e, payload.get("msgtype"))
            return {"errcode": -1, "errmsg": str(e)}
