#!/usr/bin/env python3
"""
poke_deal_scanner.py
--------------------
Scans eBay for underpriced Pokemon cards on your watchlist and alerts you.

WHAT IT DOES
  * For each card, searches eBay (UK + US marketplaces, plus Japan/Singapore-based
    sellers via item-location filters) for both auctions and Buy-It-Now listings.
  * Converts every price to GBP and estimates the *landed* cost (item + shipping +
    a rough import-tax estimate for your UK / SG addresses).
  * Works out a baseline "fair price" per card -- either a value you set, or the
    median of the current active listings -- and flags anything that comes in
    significantly under it (default: 25% below).
  * Records each run's median to build your own price trend over time.
  * Prints a report and (optionally) emails you only the NEW deals.

WHAT IT NEEDS (all free)
  1. An eBay developer account -> a "Production" App ID (Client ID) + Cert ID
     (Client Secret).  https://developer.ebay.com  -> takes ~10 min.
  2. Python 3.9+  and  `pip install requests`.
  3. (Optional) a Gmail address + App Password to receive email alerts.

Set these as environment variables (or GitHub Actions secrets):
  EBAY_CLIENT_ID, EBAY_CLIENT_SECRET
  ALERT_EMAIL_TO, ALERT_EMAIL_FROM, ALERT_EMAIL_APP_PASSWORD   (email is optional)

Then just:  python poke_deal_scanner.py
Schedule it daily with the included GitHub Actions workflow, or a cron job.
"""

import os
import json
import base64
import smtplib
import statistics
from email.mime.text import MIMEText
from datetime import datetime, timezone
from urllib.parse import quote

import requests

# ============================================================================
# 1. YOUR WATCHLIST -- one line per card. This is the only part you edit.
# ----------------------------------------------------------------------------
# Every set/number below is verified against the PkmnCards illustrator database.
#   name/set : how it shows on the dashboard
#   q        : words sent to eBay        must : ALL must appear in the title
#              (a nested list = "any one of these")
#   ed       : "1st" | "unlimited" | "any"      lang : "en" | "jp"
#   var      : "reverse" (reverse holo) | "holo" | "regular" (no holo) | "any"
#   img      : pokemontcg.io code for the reference thumbnail ("" = none)
# RULE OF THUMB: 1st Edition only exists in English up to Neo Destiny (2002);
# reverse holos only exist from 2002 onward. So vintage = regular, modern = reverse.
# ============================================================================
CARDS = [
 # --- vintage English: 1st Edition possible, no reverse holo exists ---
 dict(name="Slowpoke", set="Neo Genesis 73/111 · 1st Ed", q="Slowpoke Neo Genesis 73 1st Edition",
      must=["slowpoke",["neo genesis","73/111"]], ed="1st", lang="en", var="any", img="neo1/73", note="Komiya"),
 dict(name="Pokémon March", set="Neo Genesis 102/111 · Trainer · 1st Ed", q="Pokemon March Neo Genesis 102 1st Edition",
      must=["march",["neo genesis","102/111"]], ed="1st", lang="en", var="any", img="neo1/102", note="Komiya, Trainer"),
 dict(name="Delibird", set="Neo Revelation 5/64 · Holo · 1st Ed", q="Delibird Neo Revelation 5/64 Holo 1st Edition",
      must=["delibird",["neo revelation","5/64"],["holo","holographic","holofoil"]], ed="1st", lang="en",
      var="holo", excl=["non-holo","non holo","nonholo"], img="neo3/5", note="Komiya"),
 dict(name="Octillery", set="Neo Revelation 34/64 · 1st Ed", q="Octillery Neo Revelation 34 1st Edition",
      must=["octillery",["neo revelation","34/64"]], ed="1st", lang="en", var="any", img="neo3/34", note="Komiya"),
 dict(name="Light Slowbro", set="Neo Destiny 51/105 · 1st Ed", q="Light Slowbro Neo Destiny 51 1st Edition",
      must=["light slowbro",["neo destiny","51/105"]], ed="1st", lang="en", var="any", img="neo4/51", note="Komiya"),
 dict(name="Dark Omastar", set="Neo Destiny 19/105 · 1st Ed", q="Dark Omastar Neo Destiny 19 1st Edition",
      must=["dark omastar",["neo destiny","19/105"]], ed="1st", lang="en", var="any", img="neo4/19", note="Komiya"),
 dict(name="Dark Omanyte", set="Neo Destiny 37/105 · 1st Ed", q="Dark Omanyte Neo Destiny 37 1st Edition",
      must=["dark omanyte",["neo destiny","37/105"]], ed="1st", lang="en", var="any", img="neo4/37", note="Komiya"),
 dict(name="Ledyba", set="Neo Destiny 71/105 · 1st Ed · Common", q="Ledyba Neo Destiny 71 1st Edition",
      must=["ledyba",["neo destiny","71/105"]], ed="1st", lang="en", var="regular", img="neo4/71", note="Komiya"),
 dict(name="Light Machamp", set="Neo Destiny 25/105 · 1st Ed", q="Light Machamp Neo Destiny 25 1st Edition",
      must=["light machamp",["neo destiny","25/105"]], ed="1st", lang="en", var="any", img="neo4/25", note="illus. Miki Tanaka"),
 dict(name="Smeargle", set="Wizards Black Star Promo #32", q="Smeargle Black Star Promo 32",
      must=["smeargle",["promo","32"]], ed="any", lang="en", var="any", img="basep/32", note="Komiya, promo"),

 # --- e-Card era English (2002-03): no 1st Ed; reverse holo preferred ---
 dict(name="Cubone", set="Expedition 103/165 · Rev Holo", q="Cubone Expedition 103 reverse holo",
      must=["cubone",["expedition","103/165"],["reverse","rev holo"]], ed="any", lang="en", var="reverse", img="ecard1/103", note="Komiya"),
 dict(name="Pidgey", set="Expedition 123/165 · Rev Holo", q="Pidgey Expedition 123 reverse holo",
      must=["pidgey",["expedition","123/165"],["reverse","rev holo"]], ed="any", lang="en", var="reverse", img="ecard1/123", note="Komiya"),
 dict(name="Hitmonchan", set="Aquapolis 81/147 · Rev Holo", q="Hitmonchan Aquapolis 81 reverse holo",
      must=["hitmonchan",["aquapolis","81/147"],["reverse","rev holo"]], ed="any", lang="en", var="reverse", img="ecard2/81", note="Komiya"),
 dict(name="Tyrogue", set="Aquapolis 63/147 · Rev Holo", q="Tyrogue Aquapolis 63 reverse holo",
      must=["tyrogue",["aquapolis","63/147"],["reverse","rev holo"]], ed="any", lang="en", var="reverse", img="ecard2/63", note="Komiya"),
 dict(name="Hitmontop", set="Aquapolis 82/147 · Rev Holo", q="Hitmontop Aquapolis 82 reverse holo",
      must=["hitmontop",["aquapolis","82/147"],["reverse","rev holo"]], ed="any", lang="en", var="reverse", img="ecard2/82", note="Komiya"),
 dict(name="Dugtrio", set="Skyridge 52/144 · Rev Holo", q="Dugtrio Skyridge 52 reverse holo",
      must=["dugtrio",["skyridge","52/144"],["reverse","rev holo"]], ed="any", lang="en", var="reverse", img="ecard3/52", note="Komiya"),

 # --- EX era English (2003-07): reverse holo preferred ---
 dict(name="Magnemite", set="EX Dragon 61/97 · Rev Holo", q="Magnemite EX Dragon 61 reverse holo",
      must=["magnemite",["dragon","61/97"],["reverse","rev holo"]], ed="any", lang="en", var="reverse", img="ex3/61", note="Komiya - verify vs Trainer Kit"),
 dict(name="Exeggcute", set="EX FireRed & LeafGreen 33/112 · Rev Holo", q="Exeggcute FireRed LeafGreen 33 reverse holo",
      must=["exeggcute",["firered","leafgreen","33/112"],["reverse","rev holo"]], ed="any", lang="en", var="reverse", img="ex6/33", note="Komiya"),
 dict(name="Swinub", set="EX Team Rocket Returns 79/109 · Rev Holo", q="Swinub Team Rocket Returns 79 reverse holo",
      must=["swinub",["team rocket returns","79/109"],["reverse","rev holo"]], ed="any", lang="en", var="reverse", img="ex7/79", note="Komiya"),
 dict(name="Wingull", set="EX Deoxys 81/107 · Rev Holo", q="Wingull EX Deoxys 81 reverse holo",
      must=["wingull",["deoxys","81/107"],["reverse","rev holo"]], ed="any", lang="en", var="reverse", img="ex8/81", note="Komiya"),
 dict(name="Pelipper", set="EX Deoxys 21/107 · Rev Holo", q="Pelipper EX Deoxys 21 reverse holo",
      must=["pelipper",["deoxys","21/107"],["reverse","rev holo"]], ed="any", lang="en", var="reverse", img="ex8/21", note="Komiya"),
 dict(name="Miltank", set="EX Unseen Forces 42/115 · Rev Holo", q="Miltank Unseen Forces 42 reverse holo",
      must=["miltank",["unseen forces","42/115"],["reverse","rev holo"]], ed="any", lang="en", var="reverse", img="ex11/42", note="Komiya"),
 dict(name="Sandshrew", set="EX Delta Species 82/113 · Rev Holo", q="Sandshrew Delta Species 82 reverse holo",
      must=["sandshrew",["delta species","82/113"],["reverse","rev holo"]], ed="any", lang="en", var="reverse", img="ex13/82", note="Komiya"),
 dict(name="Drowzee", set="EX Delta Species 67/113 · Rev Holo", q="Drowzee Delta Species 67 reverse holo",
      must=["drowzee",["delta species","67/113"],["reverse","rev holo"]], ed="any", lang="en", var="reverse", img="ex13/67", note="Komiya"),
 dict(name="Dugtrio (CG)", set="EX Crystal Guardians 5/100 · Rev Holo", q="Dugtrio Crystal Guardians 5 reverse holo",
      must=["dugtrio",["crystal guardians","5/100"],["reverse","rev holo"]], ed="any", lang="en", var="reverse", img="ex14/5", note="Komiya"),

 # --- XY / Sun&Moon / Sword&Shield English: reverse holo preferred ---
 dict(name="Exeggcute (ROS)", set="Roaring Skies 1/108 · Rev Holo", q="Exeggcute Roaring Skies 1 reverse holo",
      must=["exeggcute",["roaring skies","1/108"],["reverse","rev holo"]], ed="any", lang="en", var="reverse", img="xy6/1", note="Komiya"),
 dict(name="Gulpin", set="Generations RC12 · fork-balancing", q="Gulpin Generations Radiant Collection RC12",
      must=["gulpin",["rc12","rc 12","radiant collection"]], ed="any", lang="en", var="any",
      excl=["stellar","sv7"], img="g1/RC12", note="Komiya"),
 dict(name="Clefairy", set="BREAKpoint 81/122 · Rev Holo", q="Clefairy BREAKpoint 81 reverse holo",
      must=["clefairy",["breakpoint","81/122"],["reverse","rev holo"]], ed="any", lang="en", var="reverse", img="xy9/81", note="Komiya"),
 dict(name="Hypno", set="BREAKpoint 51/122 · Rev Holo", q="Hypno BREAKpoint 51 reverse holo",
      must=["hypno",["breakpoint","51/122"],["reverse","rev holo"]], ed="any", lang="en", var="reverse", img="xy9/51", note="Komiya"),
 dict(name="Nosepass", set="Guardians Rising 69/145 · Rev Holo", q="Nosepass Guardians Rising 69 reverse holo",
      must=["nosepass",["guardians rising","69/145"],["reverse","rev holo"]], ed="any", lang="en", var="reverse", img="sm2/69", note="Komiya"),
 dict(name="Plusle", set="Shining Legends 33/73 · Rev Holo", q="Plusle Shining Legends 33 reverse holo",
      must=["plusle",["shining legends","33/73"],["reverse","rev holo"]], ed="any", lang="en", var="reverse", img="sm35/33", note="Komiya"),
 dict(name="Onix", set="Lost Thunder 109/214 · Rev Holo", q="Onix Lost Thunder 109 reverse holo",
      must=["onix",["lost thunder","109/214"],["reverse","rev holo"]], ed="any", lang="en", var="reverse", img="sm8/109", note="Komiya"),
 dict(name="Wailmer", set="Cosmic Eclipse 45/236 · Rev Holo", q="Wailmer Cosmic Eclipse 45 reverse holo",
      must=["wailmer",["cosmic eclipse","45/236"],["reverse","rev holo"]], ed="any", lang="en", var="reverse", img="sm12/45", note="Komiya"),
 dict(name="Croconaw", set="Fusion Strike 056/264 · Rev Holo", q="Croconaw Fusion Strike 56 reverse holo",
      must=["croconaw",["fusion strike","056/264","56/264"],["reverse","rev holo"]], ed="any", lang="en", var="reverse", img="swsh8/56", note="Komiya"),
 dict(name="Farfetch'd", set="Brilliant Stars 115/172 · Rev Holo", q="Farfetchd Brilliant Stars 115 reverse holo",
      must=["farfetch",["brilliant stars","115/172"],["reverse","rev holo"]], ed="any", lang="en", var="reverse", img="swsh9/115", note="Komiya"),

 # --- Japanese-only (no English print exists) ---
 dict(name="Snorlax", set="JP Vending series · glossy", q="Snorlax Japanese vending glossy Pokemon",
      must=["snorlax",["vending","glossy","expansion sheet"]], ed="any", lang="jp", var="any", img="", note="JP vending"),
 dict(name="Growlithe", set="JP Vending S3 · glossy", q="Growlithe Japanese vending glossy Pokemon",
      must=["growlithe",["vending","glossy","expansion sheet"]], ed="any", lang="jp", var="any", img="", note="JP vending, no EN"),
 dict(name="Weedle", set="JP Vending series · glossy", q="Weedle Japanese vending glossy Pokemon",
      must=["weedle",["vending","glossy","expansion sheet"]], ed="any", lang="jp", var="any", img="", note="JP vending"),
 dict(name="Onix (JP)", set="JP Vending series · glossy", q="Onix Japanese vending glossy Pokemon",
      must=["onix",["vending","glossy","expansion sheet"]], ed="any", lang="jp", var="any", img="", note="JP vending"),
 dict(name="Seadra", set="JP Vending series · glossy", q="Seadra Japanese vending glossy Pokemon",
      must=["seadra",["vending","glossy","expansion sheet"]], ed="any", lang="jp", var="any", img="", note="JP vending"),
 dict(name="Janine's Arbok", set="JP VS series · 1st Ed", q="Janine's Arbok VS Japanese 1st Edition",
      must=["arbok",["janine","anzu","vs series"," vs "]], ed="any", lang="jp", var="any", img="", note="JP VS, no EN"),
 dict(name="Janine's Weezing", set="JP VS series · 1st Ed", q="Janine's Weezing VS Japanese 1st Edition",
      must=["weezing",["janine","anzu","vs series"," vs "]], ed="any", lang="jp", var="any", img="", note="JP VS, no EN"),
 dict(name="Janine's Shuckle", set="JP VS series · 1st Ed", q="Janine's Shuckle VS Japanese 1st Edition",
      must=["shuckle",["janine","anzu","vs series"," vs "]], ed="any", lang="jp", var="any", img="", note="JP VS, no EN"),
 dict(name="Psyduck (JP)", set="JP promo", q="Psyduck Japanese promo Komiya Pokemon",
      must=["psyduck"], ed="any", lang="jp", var="any", img="", note="JP promo - verify"),
 dict(name="Bellsprout (JP)", set="JP EX era", q="Bellsprout Japanese Pokemon card",
      must=["bellsprout"], ed="any", lang="jp", var="any", img="", note="JP - verify set"),
 dict(name="Tyrogue (JP)", set="JP e-Card era", q="Tyrogue Japanese Pokemon card e-card",
      must=["tyrogue"], ed="any", lang="jp", var="any", img="", note="JP - verify set"),
]

WATCHLIST = [{"query": c["q"], "must": c["must"], "edition": c["ed"], "language": c["lang"],
              "variant": c["var"], "exclude": c.get("excl", []), "require_nm": True,
              "fair_gbp": None, "note": c["note"]} for c in CARDS]

# Applied to EVERY card. These are the usual median-polluters for vintage singles.
GLOBAL_EXCLUDE = [
    "psa", "bgs", "cgc", "sgc", "graded", "gem mint 10",          # raw only
    "proxy", "custom", "replica", "orica", "fake", "not real", "art card",
    "jumbo", "oversized",
    "sealed", " booster", " pack", " box", " tin", " etb",         # sealed product
    " lot ", "lot of", "bundle", "playset", "complete set", "whole set",
    "choose", "pick a", "pick your", "you choose", "u pick", "select your",
]

# ============================================================================
# 2. SETTINGS
# ============================================================================
DISCOUNT_THRESHOLD = 0.25          # flag listings >=25% under the baseline
MARKETPLACES       = ["EBAY_GB", "EBAY_US"]   # well-supported Browse markets
JP_SG_VIA_LOCATION = ["JP", "SG"]  # also surface sellers located here
DEST               = "UK"          # your delivery country: "UK" or "SG"
MAX_PER_QUERY      = 50
STATE_FILE         = "scanner_state.json"   # remembers seen items + price history

# Condition verification: read eBay's structured "Card Condition" aspect per item
# so raw-NM is confirmed by eBay's own data, not just the title.
VERIFY_CONDITION   = True   # set False to skip the per-item calls (title-only)
STRICT_NM_ONLY     = False  # True = also drop items whose condition can't be read
MAX_DETAIL_CHECKS  = 25     # cap per-item detail calls per card (protects rate limit)

# Rough FX to GBP -- refreshed live if possible, else these fallbacks are used.
FX_TO_GBP = {"GBP": 1.0, "USD": 0.743, "EUR": 0.85, "JPY": 0.0050, "SGD": 0.578}

# ============================================================================
# 3. eBAY API PLUMBING
# ============================================================================
def get_ebay_token(client_id, client_secret):
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    r = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers={"Authorization": f"Basic {creds}",
                 "Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "client_credentials",
              "scope": "https://api.ebay.com/oauth/api_scope"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def search_ebay(token, marketplace, query, item_location=None):
    """One Browse API search. Returns a list of simplified listing dicts."""
    filt = "buyingOptions:{FIXED_PRICE|AUCTION}"
    if item_location:
        filt += f",itemLocationCountry:{item_location}"
    params = {"q": query, "limit": MAX_PER_QUERY, "filter": filt}
    try:
        r = requests.get(
            "https://api.ebay.com/buy/browse/v1/item_summary/search",
            headers={"Authorization": f"Bearer {token}",
                     "X-EBAY-C-MARKETPLACE-ID": marketplace},
            params=params, timeout=30,
        )
        r.raise_for_status()
    except requests.HTTPError as e:
        print(f"   ! {marketplace}/{item_location or 'any'} search failed: {e}")
        return []

    out = []
    for it in r.json().get("itemSummaries", []) or []:
        price = it.get("price", {}) or {}
        # Some listings (multi-variation, "see price in cart", certain auction
        # formats) return no price. Skip them -- defaulting to 0 would poison the
        # median and make every one look like a -100% deal.
        pval = price.get("value")
        try:
            pval = float(pval)
        except (TypeError, ValueError):
            continue
        if pval <= 0:
            continue
        # For auctions, prefer the live current bid when eBay supplies it.
        bid = (it.get("currentBidPrice") or {}).get("value")
        try:
            bid = float(bid)
        except (TypeError, ValueError):
            bid = None
        opts = it.get("buyingOptions") or []
        if bid and bid > 0 and "AUCTION" in opts:
            pval = bid
        ship = 0.0
        for s in (it.get("shippingOptions") or []):
            sc = (s.get("shippingCost") or {}).get("value")
            if sc is not None:
                ship = float(sc)
                break
        out.append({
            "id": it.get("itemId"),
            "title": it.get("title", ""),
            "price": pval,
            "currency": price.get("currency", "GBP"),
            "shipping": ship,
            "condition": (it.get("condition") or "").lower(),
            "buying": ",".join(opts),
            "bids": it.get("bidCount", 0) or 0,
            "ends": it.get("itemEndDate", "") or "",
            "seller_country": (it.get("itemLocation") or {}).get("country", ""),
            "url": it.get("itemWebUrl", ""),
            "image": (it.get("image") or {}).get("imageUrl", ""),
            "marketplace": marketplace,
        })
    return out


# ============================================================================
# 4. PRICING HELPERS
# ============================================================================
def refresh_fx():
    """Best-effort live FX; silently keeps fallbacks if offline."""
    try:
        r = requests.get("https://open.er-api.com/v6/latest/GBP", timeout=15)
        rates = r.json().get("rates", {})
        for cur in list(FX_TO_GBP):
            if cur in rates and rates[cur]:
                FX_TO_GBP[cur] = 1.0 / float(rates[cur])
    except Exception:
        pass


def to_gbp(value, currency):
    return value * FX_TO_GBP.get(currency, 1.0)


def card_gbp(listing):
    """The card price only (item, converted to GBP) -- no shipping, no tax.
    This is the value used for the median, the trend, and deal detection."""
    return to_gbp(listing["price"], listing["currency"])


def ship_gbp(listing):
    return to_gbp(listing["shipping"], listing["currency"])


def import_tax_gbp(base_gbp, src, dest):
    """Rough import tax on (item + shipping) for a given destination.
    UK: 20% VAT only kicks in above £135 (eBay collects it at checkout below that).
    SG: 9% GST on imported goods. Domestic (same country) = no import tax."""
    if dest == "UK":
        if src == "GB":
            return 0.0
        return round(base_gbp * 0.20, 2) if base_gbp > 135 else 0.0
    if dest == "SG":
        if src == "SG":
            return 0.0
        return round(base_gbp * 0.09, 2)
    return 0.0


def cost_breakdown(listing):
    """Card price, shipping, and import tax kept SEPARATE, with UK and SG totals
    for reference. All values in GBP."""
    card = card_gbp(listing)
    ship = ship_gbp(listing)
    src = listing.get("seller_country", "") or "?"
    base = card + ship
    tax_uk = import_tax_gbp(base, src, "UK")
    tax_sg = import_tax_gbp(base, src, "SG")
    return {
        "card_gbp": round(card),
        "ship_gbp": round(ship),
        "tax_uk_gbp": round(tax_uk),
        "tax_sg_gbp": round(tax_sg),
        "total_uk_gbp": round(base + tax_uk),
        "total_sg_gbp": round(base + tax_sg),
        "source": src,
    }


FIRST_ED = ["1st edition", "1st ed", "first edition", "1st-edition", "first ed"]


def matches(listing, card):
    """True only if the listing title genuinely is this card/edition/language.

    Runs BEFORE any price maths, so the median and deal detection are computed
    from real matches -- e.g. a 1st-Edition Delibird, not an Unlimited one, a
    graded slab, or a 'choose your card' bulk listing.
    """
    t = " " + listing["title"].lower() + " "

    # 1) global block-list (graded, lots, sealed, proxies, multi-card listings)
    for bad in GLOBAL_EXCLUDE:
        if bad in t:
            return False
    # 2) card-specific excludes
    for bad in card.get("exclude", []):
        if bad.lower() in t:
            return False
    # 3) language
    lang = card.get("language", "en")
    if lang == "en" and ("japanese" in t or "japan " in t):
        return False
    if lang == "jp" and not any(k in t for k in ("japanese", "japan", "vending", "glossy")):
        return False
    # 4) edition
    ed = card.get("edition", "any")
    is_first = any(f in t for f in FIRST_ED)
    if ed == "1st" and (not is_first or "unlimited" in t):
        return False
    if ed == "unlimited" and is_first:
        return False
    # 4b) variant: reverse holo / holo / plain regular
    var = card.get("variant", "any")
    is_rev = ("reverse" in t) or ("rev holo" in t) or ("rev. holo" in t)
    is_holo = (("holo" in t) or ("foil" in t)) and "non-holo" not in t and "non holo" not in t
    if var == "reverse" and not is_rev:
        return False
    if var == "holo" and (not is_holo or is_rev):
        return False
    if var == "regular" and (is_rev or is_holo):
        return False
    # 5) must-have terms (a list entry = at least one of the alternatives)
    for m in card.get("must", []):
        if isinstance(m, (list, tuple)):
            if not any(alt.lower() in t for alt in m):
                return False
        elif m.lower() not in t:
            return False
    return True


def is_raw_nm(listing):
    """Drop visibly damaged/played copies. Absence of a grade term = raw.

    Note: eBay's precise NM/LP aspect isn't returned by the search endpoint, so
    this catches obvious wear from the title/condition only. For strict grading
    you'd fetch each item's aspects via the Browse getItem call (see README)."""
    t = (listing["title"] + " " + listing["condition"]).lower()
    damaged = ["damaged", "heavily played", " hp ", "poor", "creased", "crease",
               "water damage", "bent", "torn", "ripped", "played condition", " lp/hp"]
    return not any(d in t for d in damaged)


def classify_condition(blob, cond_id=""):
    """Bucket eBay's condition text into graded / played / nm / unknown.

    eBay's five ungraded TCG values are: 'Near mint or better',
    'Lightly played (excellent)', 'Moderately played (very good)',
    'Heavily played (poor)', 'Damaged'. Graded slabs use conditionId 2750."""
    b = blob.lower()
    graded = (cond_id == "2750"
              or any(g in b for g in ("psa", "bgs", "cgc", "sgc",
                                      "professional grader", "certification number"))
              or ("graded" in b and "ungraded" not in b))
    if graded:
        return "graded"
    if "near mint" in b:
        return "nm"
    unplayed = ("unplayed" in b) or ("never played" in b)
    if "damaged" in b or ("played" in b and not unplayed):
        return "played"
    return "unknown"


def get_item_condition(token, marketplace, item_id):
    """Fetch one item's detail and return its condition bucket (see classify)."""
    try:
        r = requests.get(
            f"https://api.ebay.com/buy/browse/v1/item/{quote(item_id, safe='')}",
            headers={"Authorization": f"Bearer {token}",
                     "X-EBAY-C-MARKETPLACE-ID": marketplace}, timeout=30)
        if r.status_code != 200:
            return "unknown"
        it = r.json()
    except Exception:
        return "unknown"
    parts = [str(it.get("condition", "")), str(it.get("conditionDescription", ""))]
    for a in (it.get("localizedAspects") or []):
        parts.append(f"{a.get('name','')} {a.get('value','')}")
    for d in (it.get("conditionDescriptors") or []):
        parts.append(str(d.get("name", "")))
        for v in (d.get("values") or []):
            parts.append(str(v.get("content", "") or v.get("value", "")))
    return classify_condition(" ".join(parts), str(it.get("conditionId", "")))


def verify_nm(token, listings):
    """Keep only genuinely raw Near-Mint-or-better listings, using eBay's own
    per-item condition aspect. Cheapest are checked first (they're the deal
    candidates that matter most); anything past the cap falls back to the title."""
    if not VERIFY_CONDITION:
        return [l for l in listings if is_raw_nm(l)]
    kept, checks = [], 0
    for l in sorted(listings, key=card_gbp):
        if checks >= MAX_DETAIL_CHECKS:
            if is_raw_nm(l):
                kept.append(l)
            continue
        checks += 1
        cond = get_item_condition(token, l["marketplace"], l["id"])
        l["verified_condition"] = cond
        if cond == "nm":
            kept.append(l)
        elif cond in ("played", "graded"):
            continue
        else:  # unknown -> fall back to the title heuristic unless strict
            if not STRICT_NM_ONLY and is_raw_nm(l):
                kept.append(l)
    return kept


# ============================================================================
# 5. STATE (seen items + price-trend history)
# ============================================================================
# Reference card art (public Pokemon TCG database). Used for the dashboard
# thumbnail when a live eBay listing image isn't available. Vending cards
# aren't in this database, so they fall back to a placeholder.
DISPLAY = {c["q"]: (c["name"], c["set"]) for c in CARDS}


REF_IMAGES = {c["q"]: f"https://images.pokemontcg.io/{c['img']}.png"
              for c in CARDS if c.get("img")}


def load_targets():
    """Per-card target prices you set in the dashboard (targets.json)."""
    if os.path.exists("targets.json"):
        try:
            with open("targets.json") as f:
                return {k: float(v) for k, v in json.load(f).items() if v}
        except Exception:
            pass
    return {}


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"seen": [], "history": {}}


def save_state(state):
    state["seen"] = state["seen"][-5000:]   # cap the memory of alerted items
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ============================================================================
# 6. MAIN
# ============================================================================
def run():
    cid = os.environ.get("EBAY_CLIENT_ID")
    secret = os.environ.get("EBAY_CLIENT_SECRET")
    if not cid or not secret:
        raise SystemExit("Set EBAY_CLIENT_ID and EBAY_CLIENT_SECRET first "
                         "(see the README).")

    refresh_fx()
    token = get_ebay_token(cid, secret)
    state = load_state()
    targets = load_targets()
    seen = set(state["seen"])
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    all_deals = []
    dashboard_cards = []

    for card in WATCHLIST:
        q = card["query"]
        print(f"\n== {q}   ({card['note']})")
        listings = []
        for mkt in MARKETPLACES:
            listings += search_ebay(token, mkt, q)
            for loc in JP_SG_VIA_LOCATION:
                listings += search_ebay(token, mkt, q, item_location=loc)

        # de-duplicate by item id
        uniq = list({l["id"]: l for l in listings if l["id"]}.values())
        raw_hits = len(uniq)

        # keep ONLY listings that really are this card / edition / language
        uniq = [l for l in uniq if matches(l, card)]
        title_ok = len(uniq)

        # confirm raw Near-Mint via eBay's own per-item condition aspect
        if card["require_nm"]:
            uniq = verify_nm(token, uniq)

        print(f"   {raw_hits} hits -> {title_ok} card matches -> {len(uniq)} raw NM")
        if not uniq:
            print("   (nothing genuine for this exact card)")
            continue

        # --- split the two markets: auctions are in-progress, BIN is firm ---
        auctions = [l for l in uniq if "AUCTION" in l["buying"]]
        bins     = [l for l in uniq if "FIXED_PRICE" in l["buying"] and "AUCTION" not in l["buying"]]

        def stats(group):
            ps = sorted(p for p in (card_gbp(l) for l in group) if p > 0)
            if not ps:
                return None
            cheap = min(group, key=card_gbp)
            return {"count": len(ps), "median_gbp": round(statistics.median(ps)),
                    "cheapest_gbp": round(ps[0]), "url": cheap["url"],
                    "bids": cheap.get("bids", 0), "ends": cheap.get("ends", "")}

        a_stats, b_stats = stats(auctions), stats(bins)

        prices = sorted(p for p in (card_gbp(l) for l in uniq) if p > 0)
        if not prices:
            print("   (no usable prices for this card)")
            continue
        median = statistics.median(prices)
        # Baseline prefers Buy-It-Now: a half-finished auction understates value.
        market = b_stats["median_gbp"] if b_stats else median
        baseline = targets.get(q) or card["fair_gbp"] or market
        state["history"].setdefault(q, []).append({"date": today, "median_gbp": round(median, 2)})

        a_txt = f"auction £{a_stats['median_gbp']} ({a_stats['count']})" if a_stats else "auction -"
        b_txt = f"BIN £{b_stats['median_gbp']} ({b_stats['count']})" if b_stats else "BIN -"
        print(f"   {len(uniq)} listings | {a_txt} | {b_txt} | baseline £{baseline:.0f}")

        cheapest = min(uniq, key=card_gbp)
        card_deals = []
        for l in uniq:
            price = card_gbp(l)
            if price <= 0 or baseline <= 0:
                continue
            if price <= baseline * (1 - DISCOUNT_THRESHOLD):
                disc = round((1 - price / baseline) * 100)
                bd = cost_breakdown(l)
                deal = {**l, "card_gbp": round(price), "discount_pct": disc,
                        "baseline": round(baseline), "card": q, "cost": bd}
                all_deals.append(deal)
                card_deals.append(deal)
                flag = "  *** NEW" if l["id"] not in seen else ""
                print(f"     DEAL -{disc}%  card £{price:.0f} "
                      f"(landed UK £{bd['total_uk_gbp']} / SG £{bd['total_sg_gbp']})  "
                      f"[{l['buying']}]  {l['title'][:50]}{flag}")
                seen.add(l["id"])

        best = max(card_deals, key=lambda d: d["discount_pct"]) if card_deals else None
        rep = best if best else cheapest       # representative listing for the breakdown
        disp = DISPLAY.get(q, (q, ""))
        dashboard_cards.append({
            "query": q,
            "name": disp[0],
            "set": disp[1],
            "note": card["note"],
            "median_gbp": round(median),
            "cheapest_gbp": round(prices[0]),
            "auction": a_stats,
            "bin": b_stats,
            "listings": len(uniq),
            "deal_count": len(card_deals),
            "best_discount_pct": best["discount_pct"] if best else 0,
            "best_url": best["url"] if best else cheapest["url"],
            "image": (best["image"] if best else cheapest["image"]),
            "ref_image": REF_IMAGES.get(q, ""),
            "target_gbp": targets.get(q, None),
            "cost": rep["cost"] if best else cost_breakdown(cheapest),
            "history": state["history"][q][-60:],
        })

    with open("dashboard.json", "w") as f:
        json.dump({"updated": today,
                   "dest": DEST,
                   "fx_note": f"USD->GBP {FX_TO_GBP['USD']:.3f}, JPY->GBP {FX_TO_GBP['JPY']:.4f}",
                   "cards": dashboard_cards}, f, indent=2)

    state["seen"] = list(seen)
    save_state(state)

    new_deals = [d for d in all_deals if d["id"]]
    if new_deals:
        email_deals(new_deals)
    print(f"\nDone. {len(all_deals)} deals found this run.")


def email_deals(deals):
    to = os.environ.get("ALERT_EMAIL_TO")
    frm = os.environ.get("ALERT_EMAIL_FROM")
    pw = os.environ.get("ALERT_EMAIL_APP_PASSWORD")
    if not (to and frm and pw):
        print("   (email not configured -- skipping alert email)")
        return
    lines = []
    for d in sorted(deals, key=lambda x: -x["discount_pct"]):
        c = d["cost"]
        lines.append(
            f"-{d['discount_pct']}%  card £{d['card_gbp']} (baseline £{d['baseline']})  {d['buying']}\n"
            f"   from {c['source']}: card £{c['card_gbp']} + ship £{c['ship_gbp']} "
            f"+ tax UK £{c['tax_uk_gbp']} / SG £{c['tax_sg_gbp']} "
            f"= total UK £{c['total_uk_gbp']} / SG £{c['total_sg_gbp']}\n"
            f"   {d['title']}\n   {d['url']}\n")
    body = "Pokemon deals found today:\n\n" + "\n".join(lines)
    msg = MIMEText(body)
    msg["Subject"] = f"[Poke Scanner] {len(deals)} underpriced card(s)"
    msg["From"], msg["To"] = frm, to
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(frm, pw)
            s.sendmail(frm, [to], msg.as_string())
        print(f"   Emailed {len(deals)} deal(s) to {to}")
    except Exception as e:
        print(f"   ! email failed: {e}")


def test_mode(keyword=None):
    """Quick first-call sanity check: validates keys, then runs ONE card live."""
    print("=" * 60)
    print("POKE SCANNER -- test mode (validates keys + runs one card)")
    print("=" * 60)

    cid = os.environ.get("EBAY_CLIENT_ID")
    secret = os.environ.get("EBAY_CLIENT_SECRET")
    print("\n1) Credentials set?")
    if not cid or not secret:
        print("   x  EBAY_CLIENT_ID / EBAY_CLIENT_SECRET are not set. See the README.")
        return
    print(f"   ok  Client ID ...{cid[-6:]}  |  secret length {len(secret)}")

    print("\n2) Requesting an application token (this proves your keys work)...")
    try:
        token = get_ebay_token(cid, secret)
    except Exception as e:
        print(f"   x  Token request FAILED: {e}")
        print("      Most common causes:")
        print("      - keyset still shows 'disabled' -> finish the account-deletion")
        print("        opt-out on the Application Keys page (README step 4)")
        print("      - a stray space or newline pasted into the Cert ID")
        return
    print(f"   ok  Received a token ({len(token)} chars). Your keys are live.")

    card = WATCHLIST[0]
    if keyword:
        card = next((c for c in WATCHLIST if keyword.lower() in c["query"].lower()), card)
    q = card["query"]
    print(f"\n3) Live search for ONE card:  {q}")
    refresh_fx()
    listings = search_ebay(token, MARKETPLACES[0], q)
    print(f"   eBay returned {len(listings)} raw listings on {MARKETPLACES[0]}")

    matched = [l for l in listings if matches(l, card)]
    print(f"   {len(matched)} genuine matches after filtering\n")
    if matched:
        for l in sorted(matched, key=card_gbp)[:6]:
            print(f"     GBP {card_gbp(l):>6.0f}   {l['title'][:58]}")
        print("\n   ok  Matching looks sane. Run the full sweep when ready:")
        print("       python poke_deal_scanner.py")
    elif listings:
        print("   No genuine matches -- here are a few raw titles so you can see why")
        print("   (edition/language/exclusions may be filtering them):")
        for l in listings[:6]:
            print(f"     - {l['title'][:64]}")
        print("\n   Paste these to me and I'll tune the query.")
    else:
        print("   eBay returned nothing for this query -- try  --test omastar  or")
        print("   check that Production (not Sandbox) keys are being used.")


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        i = sys.argv.index("--test")
        kw = sys.argv[i + 1] if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("-") else None
        test_mode(kw)
    else:
        run()
