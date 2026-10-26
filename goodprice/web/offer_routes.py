from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, ValidationError

from goodprice.models import ListingOffer
from goodprice.offers.pricing import parse_money_cents
from goodprice.offers.service import OfferService
from goodprice.offers.types import QuoteInput
from goodprice.services.offer_pipeline import OfferPipeline
from goodprice.web.list_context import safe_return_to

router = APIRouter()


class QuoteForm(BaseModel):
    amount: str = Field(min_length=1, max_length=40)
    fingerprint: str = Field(min_length=1, max_length=64)
    valid_days: int = Field(default=7, ge=1, le=30)


@router.get("/api/listings/{listing_id}/offers")
def list_offers(request: Request, listing_id: int):
    try:
        return OfferPipeline(request.app.state.session_factory).snapshot(listing_id, datetime.now(timezone.utc))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/offers/{offer_id}/quotes")
async def confirm_quote(request: Request, offer_id: int):
    factory = request.app.state.session_factory
    with factory() as session:
        offer = session.get(ListingOffer, offer_id)
        if offer is None:
            raise HTTPException(404, "规格不存在")
        key, listing_id = offer.offer_key, offer.listing_id
    json_request = "application/json" in request.headers.get("content-type", "")
    try:
        form = QuoteForm.model_validate(await request.json() if json_request else dict(await request.form()))
        now = datetime.now(timezone.utc)
        row_id = OfferService(factory).confirm_quote(offer_id, QuoteInput(
            key, parse_money_cents(form.amount), now, now + timedelta(days=form.valid_days), form.fingerprint))
    except (ValidationError, ValueError, TypeError) as exc:
        raise HTTPException(422, "报价无效或价格证据已变化，请刷新后确认") from exc
    if json_request:
        return {"id": row_id}
    return RedirectResponse(f"/listings/{listing_id}?" + urlencode({"return_to": safe_return_to(request.query_params.get("return_to"))}), status_code=303)


@router.post("/quotes/{quote_id}/revoke")
async def revoke_quote(request: Request, quote_id: int):
    from goodprice.models import ManualQuote

    factory = request.app.state.session_factory
    with factory() as session:
        quote = session.get(ManualQuote, quote_id)
        if quote is None:
            raise HTTPException(404, "报价不存在")
        listing_id = session.get(ListingOffer, quote.offer_id).listing_id
    OfferService(factory).revoke_quote(quote_id)
    if "application/json" in request.headers.get("content-type", ""):
        return {"revoked": True}
    return RedirectResponse(f"/listings/{listing_id}?" + urlencode({"return_to": safe_return_to(request.query_params.get("return_to"))}), status_code=303)
