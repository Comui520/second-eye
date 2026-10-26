import shutil

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import ValidationError

from goodprice.config import Settings
from goodprice.media.budget import MediaBudget
from goodprice.models import MediaAsset

router = APIRouter()
MEDIA_FIELDS = {name for name in Settings.model_fields if name.startswith("media_")} | {"video_analysis_enabled"}
MEDIA_LABELS = dict(zip([
    "media_image_count", "media_video_count", "media_frames_per_video", "media_image_quota",
    "media_frame_quota", "media_total_limit", "media_model_limit", "media_video_max_mib",
    "media_video_max_seconds", "media_cache_mib", "media_retention_days", "media_download_timeout",
    "media_decode_timeout", "media_request_max_mib", "media_ffmpeg_threads"], [
    "每商品原图保留数（1–32）", "每商品视频数（1–3）", "每视频采样帧数（1–24）",
    "每请求原图配额（0–16）", "每请求帧配额（0–16）", "每请求总输入上限（1–16）",
    "已确认模型上限（未知为8，最高16）", "视频大小上限（10–200 MiB）",
    "视频时长上限（15–300秒）", "缓存容量（256–10240 MiB）", "保留天数（1–30）",
    "下载超时（10–120秒）", "解码超时（10–120秒）", "请求体上限（1–32 MiB）",
    "每进程解码线程（1–4）"], strict=True))


def media_budget(settings):
    return MediaBudget(settings.data_root / "media-cache", settings.media_cache_mib * 1024**2,
                       settings.media_retention_days * 86400)


def capabilities(settings):
    return {"ffmpeg_available": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
            "video_enabled": settings.video_analysis_enabled,
            "total_limit": settings.media_total_limit, "model_limit": settings.media_model_limit,
            "image_quota": settings.media_image_quota, "frame_quota": settings.media_frame_quota,
            "request_max_mib": settings.media_request_max_mib}


@router.get("/api/media/capabilities")
def media_capabilities(request: Request):
    return capabilities(request.app.state.settings_service.get())


class PinnedFileResponse(FileResponse):
    def __init__(self, path, pin):
        super().__init__(path, media_type="image/jpeg", headers={"X-Content-Type-Options": "nosniff"})
        self.pin = pin

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.pin.__exit__(None, None, None)


def _pinned_response(budget, path):
    with budget.state.lock:
        path = budget.checked(path)
        if not path.is_file() or path.suffix != ".jpg":
            raise ValueError()
        pin = budget.pin([path])
        pin.__enter__()
    return PinnedFileResponse(path, pin)


@router.get("/media/{asset_id}")
def media_file(asset_id: int, request: Request):
    settings = request.app.state.settings_service.get()
    budget = media_budget(settings)
    with request.app.state.session_factory() as session:
        asset = session.get(MediaAsset, asset_id)
        if asset is None or asset.kind not in {"image", "frame"} or asset.status == "superseded":
            raise HTTPException(404, "证据文件不可用或已清理")
        if asset.relative_path:
            try:
                return _pinned_response(budget, budget.root / asset.relative_path)
            except ValueError:
                raise HTTPException(404, "证据文件不可用或已清理") from None
    # Viewing an image does not require vision to be enabled or configured.
    # Source requests use the SAME pinned public downloader and disk budget.
    if asset.kind == "image" and asset.source_url and asset.status != "unsupported":
        from goodprice.media.processing import MediaProcessor
        from goodprice.media.types import MediaCandidate

        processor = MediaProcessor(budget)
        with processor.session([MediaCandidate(asset.key, "image", asset.source_url, asset.offer_key)], settings) as (evidence, _):
            if evidence:
                image = evidence[0]
                response = _pinned_response(budget, image.local_path)
                with request.app.state.session_factory() as session:
                    row = session.get(MediaAsset, asset_id)
                    if row:
                        row.relative_path = image.local_path.relative_to(budget.root).as_posix()
                        row.content_hash = image.content_hash
                        row.status = "ready"
                        session.commit()
                return response
    raise HTTPException(404, "证据文件不可用或已清理")


@router.post("/settings/media")
async def save_media(request: Request):
    form = await request.form()
    if set(form) - MEDIA_FIELDS - {"confirm_cost", "media_form"}:
        raise HTTPException(422, "媒体分类含不可修改的字段")
    values = {k: str(v) for k, v in form.items() if k in MEDIA_FIELDS}
    if "media_form" in form:
        values["video_analysis_enabled"] = "1" if "video_analysis_enabled" in form else "0"
    service = request.app.state.settings_service
    before = service.get()
    try:
        proposed = Settings(_env_file=None, **(before.model_dump() | values))
        increases = any(getattr(proposed, k) > getattr(before, k) for k in MEDIA_FIELDS
                        if k != "media_retention_days")
        if increases and form.get("confirm_cost") != "1":
            raise HTTPException(422, "增加媒体预算需确认费用、响应延迟、内存、存储与模型限制")
        service.set_many(values)
    except ValidationError:
        raise HTTPException(422, "媒体设置无效：请检查范围、模型上限和缓存容量") from None
    return RedirectResponse("/settings#media", status_code=303)


def public_assets(request, listing_id):
    from goodprice.media.processing import MediaProcessor
    from goodprice.services.media_service import MediaService

    return MediaService(request.app.state.session_factory,
        MediaProcessor(media_budget(request.app.state.settings_service.get()))).public_assets(listing_id)
