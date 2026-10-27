from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError

from goodprice.config import SECRET_FIELDS, Settings

router = APIRouter()
SECTIONS = {
    "account": frozenset({"xianyu_cookie", "proxy", "default_crawl_interval_minutes", "default_crawl_jitter_minutes"}),
    "models": frozenset({"llm_base_url", "llm_api_key", "llm_model", "llm_api_format", "vision_base_url", "vision_api_key",
        "vision_model", "vision_api_format", "vision_enabled", "jev_enabled", "jev_auto_threshold", "jev_backend", "jev_api_key"}),
    "notifications": frozenset({"serverchan_sendkey", "serverchan_enabled", "wecom_webhook", "wecom_robot_enabled",
        "feishu_webhook", "feishu_secret", "feishu_enabled", "gotify_url", "gotify_token", "gotify_priority", "gotify_enabled"}),
    "deployment_readonly": frozenset(),
    "resources": frozenset({name for name in Settings.model_fields if name.startswith("execution_")}),
}


def save_section(service, section, form):
    fields = SECTIONS.get(section)
    if not fields:
        raise ValueError("设置分类不可修改")
    extras = {f"clear_{name}" for name in fields & SECRET_FIELDS} | {"confirm_clear", "confirm_resources"}
    if set(form) - fields - extras:
        raise ValueError("分类包含不允许修改的字段")
    clear = {name for name in fields & SECRET_FIELDS if form.get(f"clear_{name}") == "1"}
    if clear and form.get("confirm_clear") != "1":
        raise ValueError("清除凭据需要明确确认")
    values = {k: str(v) for k, v in form.items() if k in fields and (k not in SECRET_FIELDS or str(v) != "")}
    if section == "resources":
        before = service.get()
        proposed = Settings(_env_file=None, **(before.model_dump() | values))
        increase = any(getattr(proposed, k) > getattr(before, k) for k in fields
                       if k != "execution_platform_interval_seconds")
        increase |= proposed.execution_platform_interval_seconds < before.execution_platform_interval_seconds
        if increase and form.get("confirm_resources") != "1":
            raise ValueError("提高资源或访问频率需确认代价")
    for name in fields:
        if name.endswith("_enabled"):
            values[name] = "1" if str(form.get(name, "")).lower() in {"1", "true", "on", "yes"} else "0"
    return service.set_many(values, clear_keys=clear)


@router.post("/settings/sections/{section}")
async def save_settings_section(request: Request, section: str):
    from goodprice.web.routes import render_settings

    form = await request.form()
    try:
        save_section(request.app.state.settings_service, section, form)
    except (ValueError, ValidationError):
        public = {k: str(v) for k, v in form.items() if k in SECTIONS.get(section, ()) and k not in SECRET_FIELDS}
        return render_settings(request, overrides=public, error="设置值无效，未保存；请检查范围和分类字段。", status_code=422)
    return RedirectResponse(f"/settings#{section}", status_code=303)
