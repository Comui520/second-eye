from goodprice.offers.matching import match_variant, stable_offer_key


def test_stable_key_uses_sku_not_price_or_attribute_order():
    assert stable_offer_key("sku-1", {"容量": "256GB"}) == "sku:sku-1"
    assert stable_offer_key(None, {"容量": "256 GB", "型号": "iPhone 15"}) == stable_offer_key(
        None, {"型号": "iphone15", "容量": "256GB"})
    assert stable_offer_key(None, {}) is None


def test_capacity_and_model_are_not_substring_matches():
    attrs = {"型号": "iPhone 15", "容量": "256GB"}
    assert match_variant("iPhone 15 / 256GB", attrs, "") == "matched"
    assert match_variant("iPhone 15 / 128GB", attrs, "") == "mismatched"
    assert match_variant("iPhone 15 / 256GB", {"型号": "iPhone 15 Pro", "容量": "256GB"}, "") == "mismatched"


def test_unknown_or_unproved_negative_is_not_matched():
    assert match_variant("非扩容", {"容量": "256GB"}, "好机器") == "uncertain"
    assert match_variant("iPhone 15 / 256GB", {}, "iPhone 15 256GB") == "uncertain"


def test_empty_target_does_not_invent_restrictions():
    assert match_variant("", {"容量": "256GB"}, "") == "matched"
