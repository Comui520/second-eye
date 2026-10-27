"""Each provider operation performs at most one HTTP attempt."""
import json

from goodprice.analysis.jev_typesafe import TypeSafeJudger
from goodprice.analysis.judge import JevJudger
from goodprice.analysis.llm import LLMClient, parse_batch_value_json
from goodprice.analysis.media_analysis import analyze_media


class ModelProvider:
    def _client(self, settings, vision=False):
        prefix = "vision" if vision else "llm"
        return LLMClient(getattr(settings, f"{prefix}_base_url"), getattr(settings, f"{prefix}_api_key"),
            getattr(settings, f"{prefix}_model"), api_format=getattr(settings, f"{prefix}_api_format"),
            max_attempts=1, retry_delay=0, allow_image_fallback=False)

    def _judger(self, settings):
        if not settings.jev_enabled:
            return None
        if settings.jev_backend == "typesafe":
            return TypeSafeJudger(settings.jev_api_key, settings.jev_auto_threshold, single_attempt=True)
        return JevJudger(self._client(settings), settings.jev_auto_threshold)

    def enabled(self, role, settings):
        if role == "visual":
            return settings.vision_enabled and self._client(settings, True).enabled
        if role.endswith("_fallback"):
            return self._client(settings).enabled
        judger = self._judger(settings)
        return self._client(settings).enabled or bool(judger and judger.enabled)

    def requirement(self, data, settings, fallback=False):
        judger = None if fallback else self._judger(settings)
        if judger and judger.enabled:
            verdict = judger.analyze_requirement(data["title"], data["description"], data["requirement"])
            if verdict.confidence >= settings.jev_auto_threshold:
                return {"matched": verdict.matched, "reason": verdict.reason}
            return {"fallback": True}
        return self._client(settings).analyze_requirement(data["title"], data["description"], data["requirement"])

    def visual(self, candidate, selection, data, settings):
        return analyze_media(candidate, selection, self._client(settings, True), data["requirement"],
                             settings.media_request_max_mib * 1024**2, data["description"])

    def value(self, items, settings, fallback=False):
        judger = None if fallback else self._judger(settings)
        if judger and judger.enabled:
            if len(items) != 1:
                raise ValueError("类型化单件判断必须逐请求调度")
            result = judger.analyze_value(items[0])
            if result["probability"] < settings.jev_auto_threshold:
                return {"fallback": True}
            key = items[0]["external_id"]
            return {"scores": {key: result["value_score"]}, "best": key, "reasons": {key: result["reason"]}}
        from goodprice.analysis.prompts import INDEPENDENT_VALUE_SYSTEM
        client = self._client(settings)
        return client.complete(client.payload([{"type": "text", "text": json.dumps(items, ensure_ascii=False)}],
            system=INDEPENDENT_VALUE_SYSTEM), parser=parse_batch_value_json)
