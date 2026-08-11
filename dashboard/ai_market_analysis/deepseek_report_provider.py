"""DeepSeek report provider isolated from PaperService and the legacy AI Brief."""
from __future__ import annotations
import json, os, time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from .canonical import stable_hash
from .report_provider import ProviderError, ProviderResult
from .versions import AI_REPORT_PROVIDER_VERSION
from .live_provider_guard import assert_live_provider_allowed


class DeepSeekAIReportProvider:
    provider_name="deepseek"; supports_structured_output=True
    endpoint="https://api.deepseek.com/chat/completions"
    def __init__(self, model: str | None=None, timeout: int | None=None,
                 api_key_file: str | Path | None=None, api_key: str | None=None):
        self.model=model or os.getenv("AI_REPORT_MODEL", "")
        self.timeout=int(timeout or os.getenv("AI_REPORT_TIMEOUT_SECONDS","45"))
        self._key_file=Path(api_key_file or os.getenv("AI_REPORT_API_KEY_FILE", "")) if (api_key_file or os.getenv("AI_REPORT_API_KEY_FILE")) else None
        self._test_key=api_key
        if not self.model: raise ValueError("AI_REPORT_MODEL is required")
        if self.model not in {"deepseek-v4-flash","deepseek-v4-pro"}:raise ValueError("UNAPPROVED_DEEPSEEK_MODEL")
        if not self._key_file and not self._test_key: raise ValueError("AI_REPORT_API_KEY_FILE is required")
        if self.timeout > 45: self.timeout=45
    def _secret(self)->str:
        if self._test_key is not None:return self._test_key
        assert self._key_file is not None
        value=self._key_file.read_text(encoding="utf-8").strip()
        if not value:raise ValueError("AI_REPORT_API_KEY_FILE is empty")
        return value
    def generate(self, request: dict) -> ProviderResult:
        assert_live_provider_allowed()
        body={"model":self.model,"messages":request["messages"],"temperature":0.2,
              "max_tokens":request["max_output_tokens"],"response_format":{"type":"json_object"},
              "thinking":{"type":"disabled"},"stream":False}
        wire=json.dumps(body,ensure_ascii=False,separators=(",",":")).encode("utf-8")
        started=time.monotonic()
        try:
            req=Request(self.endpoint,data=wire,headers={"Authorization":f"Bearer {self._secret()}","Content-Type":"application/json","User-Agent":f"crypto-bot/{AI_REPORT_PROVIDER_VERSION}"})
            with urlopen(req,timeout=self.timeout) as response:  # noqa: S310 - fixed HTTPS endpoint
                raw=response.read(2_000_001)
                if len(raw)>2_000_000: raise ProviderError("RESPONSE_TOO_LARGE",retryable=False,http_status=response.status)
                payload=json.loads(raw.decode("utf-8")); choice=payload["choices"][0]; content=choice["message"]["content"] or ""
                usage=payload.get("usage") or {}
                reported={key:int(usage[key]) for key in ("prompt_tokens","completion_tokens","total_tokens","prompt_cache_hit_tokens","prompt_cache_miss_tokens") if isinstance(usage.get(key),int)}
                details=usage.get("completion_tokens_details")
                if isinstance(details,dict):reported["completion_tokens_details"]={key:int(value) for key,value in details.items() if isinstance(value,int)}
                return ProviderResult(content,payload.get("id"),str(payload.get("model") or self.model),reported,
                    choice.get("finish_reason"),response.status,int((time.monotonic()-started)*1000),stable_hash(content))
        except HTTPError as error:
            charge="FAILED_BEFORE_CHARGE" if error.code in {400,401,402,403,422} else "UNKNOWN_CHARGE_STATE"
            raise ProviderError(f"HTTP_{error.code}",retryable=False,http_status=error.code,
                                request_body_sent=True,provider_accepted=False if error.code in {400,401,402,403,422} else None,
                                charge_state=charge) from None
        except (TimeoutError,URLError):
            raise ProviderError("CONNECTION_OR_TIMEOUT",retryable=False,request_body_sent=True,
                                provider_accepted=None,charge_state="UNKNOWN_CHARGE_STATE") from None
        except (KeyError,ValueError,json.JSONDecodeError) as error:
            before_send=isinstance(error,ValueError) and str(error)=="AI_REPORT_API_KEY_FILE is empty"
            raise ProviderError("SECRET_FILE_INVALID" if before_send else "INVALID_PROVIDER_RESPONSE",retryable=False,
                                request_body_sent=False if before_send else True,
                                provider_accepted=False if before_send else True,
                                charge_state="FAILED_BEFORE_CHARGE" if before_send else "UNKNOWN_CHARGE_STATE") from None
