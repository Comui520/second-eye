from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from goodprice.execution.limits import ResourceLimits
from goodprice.resources import detect_resources, recommend_profile, resource_profile

router = APIRouter()
RESOURCE_LABELS = {
    "execution_active_runs": "同时推进的任务数（1–4）",
    "execution_crawler_workers": "抓取工作线程（1–2）",
    "execution_model_workers": "模型请求并发（1–8）",
    "execution_media_workers": "媒体处理并发（1–2）",
    "execution_platform_interval_seconds": "平台导航最小间隔（5–300秒）",
    "execution_model_rpm": "模型 HTTP 请求上限（1–60次/分钟）",
    "execution_global_items": "全局未完成步骤（1–200）",
    "execution_task_items": "单任务未完成步骤（1–60）",
    "execution_run_details": "每轮详情预算（1–100）",
    "execution_run_http_attempts": "每轮模型 HTTP 尝试（1–200）",
    "execution_run_minutes": "每轮墙钟预算（5–60分钟）",
}


@router.get("/api/resources/recommendation")
def resource_recommendation(request: Request):
    snapshot = detect_resources()
    return {"snapshot": asdict(snapshot), "recommendation": recommend_profile(snapshot), **effective_resources(request)}


def effective_resources(request):
    configured = ResourceLimits.from_settings(request.app.state.settings_service.get())
    guard = getattr(request.app.state, "resource_guard", None)
    current = guard.evaluate(configured) if guard else None
    controller = request.app.state.coordinator
    return {"configured": asdict(configured), "effective": asdict(current.limits if current else configured),
        "paused": sorted(current.paused) if current else [], "reasons": list(current.reasons) if current else [],
        "running_tasks": len(controller.running_ids()), "queued_tasks": len(controller.queued_ids())}


@router.post("/settings/resources/apply-profile")
async def apply_profile(request: Request):
    form = await request.form()
    if form.get("confirm") != "1" or set(form) - {"name", "confirm"}:
        raise HTTPException(422, "请明确确认应用资源配置")
    try:
        values = resource_profile(form.get("name", ""))
    except ValueError:
        raise HTTPException(422, "未知资源配置") from None
    runtime = request.app.state.settings_service.set_many({k: str(v) for k, v in values.items()})
    coordinator = getattr(request.app.state, "coordinator", None)
    if coordinator:
        coordinator.update_limits(ResourceLimits.from_settings(runtime))
    return RedirectResponse("/settings#resources", status_code=303)
