"""DeepSeek report provider isolated from PaperService and the legacy AI Brief."""
from __future__ import annotations
import json, os, time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from .canonical import stable_hash
from .report_provider import ProviderError, ProviderResult
from .versions import AI_REPORT_PROVIDER_VERSION
from .live_provider_guard import assert_live_provider_allowed


class DeepSeekAIReportProvider:
    provider_name="deepseek"; supports_structured_output=True
    endpoint="https://api.deepseek.com/chat/completions"
    def __init__(self, model: str | None=None, timeout: int | None=None, api_key: str | None=None):
        self.model=model or os.getenv("AI_REPORT_MODEL", "")
        self.timeout=int(timeout or os.getenv("AI_REPORT_TIMEOUT_SECONDS","45"))
        self._key=api_key or os.getenv("AI_REPORT_API_KEY", "")
        if not self.model: raise ValueError("AI_REPORT_MODEL is required")
        if not self._key: raise ValueError("AI_REPORT_API_KEY is required")
        if self.timeout > 45: self.timeout=45
    def generate(self, request: dict) -> ProviderResult:
        assert_live_provider_allowed()
        body={"model":self.model,"messages":request["messages"],"temperature":0.2,
              "max_tokens":request["max_output_tokens"],"response_format":{"type":"json_object"},
              "thinking":{"type":"disabled"},"stream":False}
        wire=json.dumps(body,ensure_ascii=False,separators=(",",":")).encode("utf-8")
        started=time.monotonic()
        req=Request(self.endpoint,data=wire,headers={"Authorization":f"Bearer {self._key}","Content-Type":"application/json","User-Agent":f"crypto-bot/{AI_REPORT_PROVIDER_VERSION}"})
        try:
            with urlopen(req,timeout=self.timeout) as response:  # noqa: S310 - fixed HTTPS endpoint
                raw=response.read(2_000_001)
                if len(raw)>2_000_000: raise ProviderError("RESPONSE_TOO_LARGE",retryable=False,http_status=response.status)
                payload=json.loads(raw.decode("utf-8")); choice=payload["choices"][0]; content=choice["message"]["content"] or ""
                usage=payload.get("usage") or {}
                return ProviderResult(content,payload.get("id"),str(payload.get("model") or self.model),
                    {"prompt_tokens":int(usage.get("prompt_tokens") or 0),"completion_tokens":int(usage.get("completion_tokens") or 0),"total_tokens":int(usage.get("total_tokens") or 0)},
                    choice.get("finish_reason"),response.status,int((time.monotonic()-started)*1000),stable_hash(content))
        except HTTPError as error:
            retryable=error.code==429 or 500<=error.code<600
            raise ProviderError(f"HTTP_{error.code}",retryable=retryable,http_status=error.code) from None
        except (TimeoutError,URLError): raise ProviderError("CONNECTION_OR_TIMEOUT",retryable=True) from None
        except (KeyError,ValueError,json.JSONDecodeError): raise ProviderError("INVALID_PROVIDER_RESPONSE",retryable=False) from None
