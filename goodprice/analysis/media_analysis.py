import base64
import hashlib
import json

from goodprice.analysis.json_extract import extract_json_object
from goodprice.media.types import MediaSelection
from goodprice.offers.types import OfferCandidate

PROMPT_VERSION = "media-evidence-1"
SYSTEM = """根据提供的商品媒体证据评估品相。卖家声明不等于观察事实。
视频帧仅覆盖列出的采样时间，不能声称看过整个视频、音频或验证动态故障。
不判断实际售价，不推断未证实的型号/容量。不把样品品相当成特定SKU实物品相。
返回JSON: condition_score(0到10或null), observations/seller_claims/conflicts/unverified(数组，
每项{text:字符串,evidence_ids:本次提供的编号数组})。任何观察和分数必须有本次证据支持。
"""


def validate_evidence_references(verdict: dict, allowed_ids: set[str]) -> dict:
    result = {}
    cited = set()
    for field in ("observations", "seller_claims", "conflicts", "unverified"):
        items = verdict.get(field, [])
        if not isinstance(items, list) or len(items) > 30:
            raise ValueError("媒体输出格式无效")
        cleaned = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("媒体输出缺少证据关联")
            ids = item.get("evidence_ids", [])
            if not isinstance(ids, list) or any(not isinstance(i, str) or i not in allowed_ids for i in ids):
                raise ValueError("模型引用了未提供的证据")
            if field == "observations" and not ids:
                raise ValueError("观察缺少证据")
            cited.update(ids)
            cleaned.append({"text": str(item.get("text", ""))[:1000], "evidence_ids": ids})
        result[field] = cleaned
    score = verdict.get("condition_score")
    if score is not None and (type(score) is not int or not 0 <= score <= 10 or not cited):
        raise ValueError("品相评分缺少有效证据或超出范围")
    result["condition_score"] = score
    return result


def analysis_key(offer, selection, client, requirement, settings, seller_text="") -> str:
    values = [PROMPT_VERSION, offer.key, dict(offer.attributes), requirement, seller_text,
        [(e.content_hash, e.offer_key, e.timestamp_ms) for e in selection.items], selection.limitations,
        {k: v for k, v in settings.model_dump(mode="json").items() if k.startswith("media_") or k == "video_analysis_enabled"},
        client.model, client.base_url, client.api_format]
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def analyze_media(offer: OfferCandidate, selection: MediaSelection, client,
                  requirement: str, request_limit_bytes: int, seller_text: str = "") -> dict:
    limitations = list(selection.limitations)
    if not selection.items:
        return {"condition_score": None, "observations": [], "limitations": limitations + ["no_visual_input"]}
    descriptors = [{"id": e.id, "kind": e.kind, "offer_key": e.offer_key,
                    "video_id": e.video_id, "timestamp_ms": e.timestamp_ms} for e in selection.items]
    content = [{"type": "text", "text": json.dumps({"offer_key": offer.key,
        "attributes": dict(offer.attributes), "requirement": requirement, "seller_text": seller_text,
        "evidence": descriptors,
        "limitations": limitations}, ensure_ascii=False)}]
    for evidence in selection.items:
        if evidence.local_path.stat().st_size * 4 // 3 > request_limit_bytes:
            raise ValueError("视觉请求超过字节预算，请降低输入设置")
        url = "data:image/jpeg;base64," + base64.b64encode(evidence.local_path.read_bytes()).decode()
        content.append({"type": "image_url", "image_url": {"url": url}})
    payload = client.payload(content, system=SYSTEM)
    # Both HTTP API formats contain the same base64 inputs. Reserve conversion
    # overhead instead of silently removing evidence to fit the request.
    if len(json.dumps(payload).encode()) + 1024 > request_limit_bytes:
        raise ValueError("视觉请求超过字节预算，请降低输入设置")
    ids = {e.id for e in selection.items}
    result = client.complete(payload, parser=lambda raw: validate_evidence_references(extract_json_object(raw), ids))
    if offer.key != "default" and any(e.offer_key != offer.key for e in selection.items):
        result["condition_score"] = None
        limitations.append("shared_media")
    if result.get("conflicts"):
        result["condition_score"] = None
        limitations.append("conflicting_evidence")
    result["limitations"] = limitations
    result["evidence_ids"] = sorted(ids)
    result["prompt_version"] = PROMPT_VERSION
    return result
