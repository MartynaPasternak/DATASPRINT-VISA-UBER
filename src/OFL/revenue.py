"""Postcode revenue pipeline: from the Visa parquet to zip_revenue.csv.

    python revenue_pipeline.py             # reuse the databases in this folder when present
    python revenue_pipeline.py --rebuild   # rebuild them from the parquet first

The folder also needs restaurants_pl_brands.py and PL.txt. The Visa extract is
the parquet one directory up, datasprint_sample_data.parquet. Databases this
script builds are written in this folder.

Outputs:
    metrics.csv, heldout_predictions.csv  model scored on the 30% of postcodes held out
    zip_revenue.csv                       zip_code, type, predicted_revenue for every
                                          postcode and restaurant type with shops
Other scripts can call run() to get zip_revenue.csv as a DataFrame.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import unicodedata
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.impute import SimpleImputer
from sklearn.metrics import r2_score
from sklearn.model_selection import GroupKFold
from xgboost import XGBRegressor

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import restaurants_pl_brands as rb  # noqa: E402

PARQUET_CANDIDATES = [
    HERE.parent / "datasprint_sample_data.parquet",
    HERE / "datasprint_sample_data.parquet",
]
GEONAMES = HERE / "PL.txt"
MERCHANT_DB = HERE / "pl_rest.duckdb"
DEMAND_DB = HERE / "zip_demand.duckdb"
ZIP_REVENUE = HERE / "zip_revenue.csv"
SEED = 0
TILL_RE = r"(( (K|KASA|POS|TERM|TERMINAL) ?[0-9]{1,3})|( SCO))+$"
HUB_MCCS = (5811, 5812, 5813, 5814)
MODEL_MCCS = (5812, 5814)
HUB_MIN_NAMES = 60
ACTIVE_MIN_CARDS = 5
MIN_SHOPS_PER_MCC = 10

BASE_FEATURES = [
    "n_shops",
    "n_brands",
    "n_competitor_shops",
    "zip_cards",
    "share_big",
]
DEMAND_FEATURES = [
    "avg_ticket",
    "foreign_share",
    "local_share",
    "leak_outside_share",
    "lodging_spend",
    "grocery_spend",
    "premium_share",
    "business_share",
    "spend_trend",
    "dinner_share",
    "weekend_share",
    "catchment_cards",
    "catchment_spend",
]
TENURE_FEATURES = ["full_year_share", "median_months_active"]
PER_SHOP_FEATURES = ["cards_per_shop", "txns_per_card", "spend_per_shop_proxy"]
FEATURES = BASE_FEATURES + DEMAND_FEATURES + TENURE_FEATURES + PER_SHOP_FEATURES

XGB_PARAMS = dict(
    n_estimators=400,
    learning_rate=0.03,
    max_depth=4,
    min_child_weight=20,
    subsample=0.8,
    colsample_bytree=0.8,
    reg_lambda=3,
    n_jobs=-1,
    random_state=0,
)
TYPE = {5812: "restaurant", 5814: "fast food"}
MIN_FULL_YEAR = 5
FOLDS = 5


def find_parquet() -> Path:
    for path in PARQUET_CANDIDATES:
        if path.is_file():
            return path
    searched = ", ".join(str(path) for path in PARQUET_CANDIDATES)
    raise FileNotFoundError(f"datasprint_sample_data.parquet not found. Looked in {searched}")


def _log(msg: str) -> None:
    print(msg, flush=True)


def _has_table(db: Path, table: str) -> bool:
    if not db.is_file():
        return False
    con = duckdb.connect(str(db), read_only=True)
    try:
        found = con.execute(
            "SELECT COUNT(*) FROM duckdb_tables() WHERE table_name = ?",
            [table],
        ).fetchone()[0]
    finally:
        con.close()
    return found > 0


def _merchant_con(parquet: Path) -> duckdb.DuckDBPyConnection:
    MERCHANT_DB.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(MERCHANT_DB))
    con.execute("SET threads TO 32")
    con.execute("SET memory_limit = '22GB'")
    con.execute("SET preserve_insertion_order = false")
    Path("/tmp/duckdb_spill_plrest").mkdir(exist_ok=True)
    con.execute("SET temp_directory = '/tmp/duckdb_spill_plrest'")
    con.execute("SET enable_progress_bar = false")
    con.execute("SET VARIABLE parquet_path = ?", [str(parquet)])
    con.execute(r"CREATE OR REPLACE MACRO digits(x) AS regexp_replace(COALESCE(CAST(x AS VARCHAR), ''), '[^0-9]', '', 'g')")
    con.execute(r"""
    CREATE OR REPLACE MACRO name_norm(n) AS
        trim(regexp_replace(replace(strip_accents(upper(COALESCE(n, ''))), 'Ł', 'L'), '[^A-Z0-9]+', ' ', 'g'))
    """)
    con.execute(f"CREATE OR REPLACE MACRO strip_tills(n) AS trim(regexp_replace(n, '{TILL_RE}', ''))")
    con.execute("""
    CREATE OR REPLACE MACRO hav_km(lat1, lon1, lat2, lon2) AS
        2 * 6371.0 * asin(sqrt(pow(sin(radians(lat2 - lat1) / 2), 2)
            + cos(radians(lat1)) * cos(radians(lat2)) * pow(sin(radians(lon2 - lon1) / 2), 2)))
    """)
    return con


def _norm_txt(value: str) -> str:
    text = str(value).upper().replace("Ł", "L")
    text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    return re.sub(r"[^A-Z0-9]+", " ", text).strip()


def build_merchant_db(parquet: Path) -> None:
    """Shop registry: tills merged, location from the first non-hub postcode, brands assigned.

    rows_train_2025 keeps located 5812/5814 merchants with at least five cards in 2025.
    The Kraków spatial model is not part of this score, so it is not built.
    """
    if _has_table(MERCHANT_DB, "rows_train_2025") and _has_table(MERCHANT_DB, "rest_cp") and _has_table(MERCHANT_DB, "pc_geo"):
        _log(f"Merchant database ready: {MERCHANT_DB.name}")
        return
    _log(f"STAGE 1/4 merchant database. Building {MERCHANT_DB.name} from {parquet.name}")
    started = time.time()
    con = _merchant_con(parquet)
    try:
        t = time.time()
        _log("STAGE 1a extract restaurant transactions")
        con.execute(f"""
        CREATE OR REPLACE TABLE rest_raw AS
        SELECT pymt_crd_acct_num_raw AS card, CAST(prch_mnth_id AS INTEGER) AS month, CAST(mrch_catg_cd AS INTEGER) AS mcc,
               CAST(cp_flag AS INTEGER) AS cp, strip_tills(name_norm(mrch_nm_raw)) AS nm1, name_norm(mrch_city_nm_raw) AS city,
               CASE WHEN length(digits(mrch_postal_code)) = 5 THEN digits(mrch_postal_code) END AS pc5,
               CAST(cs_tran_amt AS DOUBLE) AS amt
        FROM read_parquet(getvariable('parquet_path'))
        WHERE mrch_ctry_cd = 616 AND transaction_type <> 'ATM' AND CAST(mrch_catg_cd AS INTEGER) IN {HUB_MCCS}
        """)
        raw = con.execute("SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE cp = 1) AS cp_rows FROM rest_raw").fetchone()
        _log(f"rest_raw rows {raw[0]:,}, card-present {raw[1]:,}  ({time.time() - t:.0f}s)")

        geo = pd.read_csv(
            GEONAMES, sep="\t", header=None, dtype=str,
            names=["cc", "postcode", "place", "voiv", "voiv_code", "powiat", "powiat_code", "gmina", "gmina_code",
                   "lat", "lon", "accuracy"],
        )
        geo["lat"], geo["lon"] = geo["lat"].astype(float), geo["lon"].astype(float)
        geo["pc5"] = geo["postcode"].str.replace("-", "", regex=False)
        pc_geo = geo.groupby("pc5").agg(
            lat=("lat", "mean"), lon=("lon", "mean"), place=("place", "first"), powiat=("powiat", "first"),
            powiat_code=("powiat_code", "first"), voiv=("voiv", "first"), n_places=("place", "size"),
        ).reset_index()
        pc_geo["powiat_key"] = pc_geo["voiv"] + "|" + pc_geo["powiat"]
        geo["city_norm"] = geo["place"].map(_norm_txt)
        geo["powiat_key"] = geo["voiv"] + "|" + geo["powiat"]
        city_pow = geo.groupby("city_norm")["powiat_key"].agg(lambda s: s.iloc[0] if s.nunique() == 1 else None).dropna()
        con.register("pc_geo_df", pc_geo)
        con.execute("CREATE OR REPLACE TABLE pc_geo AS SELECT * FROM pc_geo_df")
        con.unregister("pc_geo_df")
        con.register("city_pow_df", city_pow.rename("powiat_key").reset_index())
        con.execute("CREATE OR REPLACE TABLE city_pow AS SELECT * FROM city_pow_df")
        con.unregister("city_pow_df")
        con.execute(f"""
        CREATE OR REPLACE TABLE hub_pm AS
        SELECT pc5, month, COUNT(DISTINCT nm1) AS n_names
        FROM rest_raw WHERE cp = 1 AND pc5 IS NOT NULL
        GROUP BY ALL HAVING COUNT(DISTINCT nm1) >= {HUB_MIN_NAMES}
        """)
        hubs = con.execute("SELECT COUNT(DISTINCT pc5) FROM hub_pm").fetchone()[0]
        _log(f"hub postcodes {hubs:,}")

        t = time.time()
        _log("STAGE 1b merchant identity")
        con.execute("""
        CREATE OR REPLACE TABLE rest_cp AS
        SELECT r.*, h.pc5 IS NOT NULL AS hub,
               COALESCE(g.powiat_key, cp_.powiat_key, 'C:' || r.city) AS place,
               g.pc5 IS NOT NULL AS pc_known
        FROM rest_raw r
        LEFT JOIN hub_pm h USING (pc5, month)
        LEFT JOIN pc_geo g ON g.pc5 = r.pc5
        LEFT JOIN city_pow cp_ ON cp_.city_norm = r.city
        WHERE r.cp = 1
        """)
        trailing_num = re.compile(r"^(.*?) ([0-9]{1,2})$")
        names = con.execute("SELECT DISTINCT nm1 FROM rest_cp").df()
        parts = names["nm1"].str.extract(trailing_num.pattern)
        names["base"] = parts[0].fillna(names["nm1"]).str.strip()
        names["num"] = parts[1].fillna("")
        con.register("names_df", names)
        con.execute("CREATE OR REPLACE TABLE name_parts AS SELECT * FROM names_df")
        con.unregister("names_df")
        co = con.execute("""
        SELECT n.base, r.place, r.pc5, r.month, list(DISTINCT n.num) AS nums
        FROM rest_cp r JOIN name_parts n USING (nm1)
        WHERE NOT r.hub AND r.pc5 IS NOT NULL
        GROUP BY ALL HAVING COUNT(DISTINCT n.num) > 1
        """).df()
        parent: dict = {}

        def find(node):
            while parent.setdefault(node, node) != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        for base, place, nums in zip(co["base"], co["place"], co["nums"]):
            root = find((base, place, nums[0]))
            for num in nums[1:]:
                parent[find((base, place, num))] = root
        grouped: dict = {}
        for node in list(parent):
            grouped.setdefault(find(node), []).append(node[2])
        num_map = []
        for root, nums in grouped.items():
            rep = "" if "" in nums else min(nums)
            num_map += [(root[0], root[1], num, rep) for num in nums]
        num_map = pd.DataFrame(num_map, columns=["base", "place", "num", "rep"])
        con.register("num_map_df", num_map)
        con.execute("CREATE OR REPLACE TABLE num_map AS SELECT * FROM num_map_df")
        con.unregister("num_map_df")
        con.execute("""
        CREATE OR REPLACE TABLE rest_cp AS
        SELECT r.*, CASE WHEN COALESCE(m.rep, n.num) = '' THEN n.base ELSE n.base || ' ' || COALESCE(m.rep, n.num) END AS store_nm
        FROM rest_cp r JOIN name_parts n USING (nm1)
        LEFT JOIN num_map m ON m.base = n.base AND m.place = r.place AND m.num = n.num
        """)
        con.execute("""
        CREATE OR REPLACE TABLE multi_outlet AS
        SELECT store_nm, place FROM (
            SELECT store_nm, place, month, COUNT(DISTINCT pc5) AS pcs
            FROM rest_cp WHERE NOT hub AND pc5 IS NOT NULL GROUP BY ALL
        ) GROUP BY ALL HAVING COUNT(*) FILTER (WHERE pcs >= 2) >= 3
        """)
        con.execute("""
        CREATE OR REPLACE TABLE rest_cp AS
        SELECT r.*, store_nm || '|' || place || CASE
            WHEN mo.store_nm IS NULL THEN ''
            WHEN r.hub OR r.pc5 IS NULL THEN '|HUB'
            ELSE '|' || r.pc5 END AS merchant
        FROM rest_cp r LEFT JOIN multi_outlet mo USING (store_nm, place)
        """)
        con.execute("""
        CREATE OR REPLACE TABLE merchant_loc AS
        WITH m AS (
            SELECT merchant, month, pc5, COUNT(*) AS n FROM rest_cp
            WHERE NOT hub AND pc_known AND NOT merchant LIKE '%|HUB' GROUP BY ALL
        )
        SELECT merchant, arg_min(pc5, (month, -n, pc5)) AS loc_pc5, MIN(month) AS loc_month FROM m GROUP BY merchant
        """)
        n_merchants = con.execute("SELECT COUNT(DISTINCT merchant) FROM rest_cp").fetchone()[0]
        _log(f"merchants {n_merchants:,}  ({time.time() - t:.0f}s)")

        t = time.time()
        _log("STAGE 1c brands")
        md = con.execute("""
        WITH s AS (SELECT merchant, mcc, SUM(amt) AS spend, COUNT(*) AS n FROM rest_cp GROUP BY ALL),
        d AS (SELECT merchant, arg_max(mcc, (spend, n, -mcc)) AS mcc FROM s GROUP BY merchant),
        a AS (SELECT merchant, any_value(store_nm) AS store_nm, any_value(place) AS place, mode(city) AS city,
                     MIN(month) AS first_month_ever, SUM(amt) AS spend_all, COUNT(DISTINCT pc5) AS booked_postcodes
              FROM rest_cp GROUP BY merchant)
        SELECT a.*, d.mcc, l.loc_pc5, g.lat, g.lon, g.place AS loc_place, g.powiat AS loc_powiat
        FROM a JOIN d USING (merchant) LEFT JOIN merchant_loc l USING (merchant) LEFT JOIN pc_geo g ON g.pc5 = l.loc_pc5
        """).df()
        place_city = md["loc_place"].fillna("").map(_norm_txt)
        md["brand"] = [
            rb.to_brand(name, f"{city} {place}".strip(), key)
            for name, city, place, key in zip(md["store_nm"], md["city"].fillna(""), place_city, md["merchant"])
        ]
        md["vending"] = md["brand"].isna()
        md["generic_name"] = md["brand"].fillna("").str.startswith("#")
        md["chain"] = md["brand"].isin(rb.CHAINS.keys())
        reg = md[~md["vending"] & ~md["generic_name"] & md["loc_pc5"].notna()].groupby(["brand", "loc_pc5"]).size()
        reg = reg[reg >= 5].reset_index()[["brand", "loc_pc5"]]
        md = md.merge(reg.assign(registration=True), on=["brand", "loc_pc5"], how="left")
        md["registration"] = md["registration"].fillna(False).astype(bool)
        md["located"] = md["loc_pc5"].notna() & ~md["registration"] & ~md["vending"]
        con.register("md_df", md)
        con.execute("CREATE OR REPLACE TABLE merchant_dim AS SELECT * FROM md_df")
        con.unregister("md_df")
        located = int(md.loc[md["mcc"].isin(MODEL_MCCS), "located"].sum())
        _log(f"located 5812/5814 merchants {located:,}  ({time.time() - t:.0f}s)")

        _log("STAGE 1d training-row registry")
        con.execute(f"""
        CREATE OR REPLACE TABLE m_tx AS
        SELECT r.merchant, d.loc_pc5 AS pc5, d.brand, r.mcc, r.card, r.month, r.amt
        FROM rest_cp r JOIN merchant_dim d USING (merchant)
        WHERE d.located AND r.mcc IN {MODEL_MCCS}
        """)
        con.execute(f"""
        CREATE OR REPLACE TABLE rows_train_2025 AS
        WITH s AS (SELECT merchant, mcc, SUM(amt) AS spend, COUNT(*) AS n FROM m_tx WHERE month BETWEEN 202501 AND 202512 GROUP BY ALL),
        d AS (SELECT merchant, arg_max(mcc, (spend, n, -mcc)) AS mcc FROM s GROUP BY merchant),
        a AS (SELECT merchant, any_value(brand) AS brand, any_value(pc5) AS pc5, SUM(amt) AS revenue,
                     COUNT(DISTINCT card) AS cards, COUNT(DISTINCT month) AS months_active
              FROM m_tx WHERE month BETWEEN 202501 AND 202512 GROUP BY merchant),
        r AS (
            SELECT a.*, d.mcc, g.lat, g.lon, g.place, g.powiat, g.powiat_key
            FROM a JOIN d USING (merchant) JOIN pc_geo g ON g.pc5 = a.pc5
            WHERE a.cards >= {ACTIVE_MIN_CARDS}
        )
        SELECT * FROM (
            SELECT *, COUNT(*) OVER (PARTITION BY mcc) AS shops_in_mcc
            FROM r
        ) WHERE shops_in_mcc >= {MIN_SHOPS_PER_MCC}
        """)
        n_rows = con.execute("SELECT COUNT(*) FROM rows_train_2025").fetchone()[0]
        _log(f"rows_train_2025 {n_rows:,}  (merchant database {time.time() - started:.0f}s)")
    finally:
        con.close()


def build_demand_db(parquet: Path, shops: pd.DataFrame) -> None:
    """Card mix for July 2025–June 2026. A training shop is left out of its own postcode sums."""
    if _has_table(DEMAND_DB, "shop_comp") and _has_table(DEMAND_DB, "shop_tx"):
        _log(f"Card database ready: {DEMAND_DB.name}")
        return
    ordered = shops.sort_values("store").reset_index(drop=True)
    rng = np.random.default_rng(SEED)
    pick = rng.permutation(len(ordered))
    n_train = int(round(0.7 * len(ordered)))
    ordered["split"] = "test"
    ordered.loc[ordered.index[pick[:n_train]], "split"] = "train"
    split_path = HERE / "shop_split.csv"
    ordered[["store", "postcode", "split"]].to_csv(split_path, index=False)

    _log(f"STAGE 3/4 card demand. Building {DEMAND_DB.name} from {parquet.name}")
    t0 = time.time()
    con = duckdb.connect(str(DEMAND_DB))
    con.execute("SET memory_limit = '24GB'")
    con.execute("SET temp_directory = '/tmp/duckdb_zip_demand'")
    con.execute("SET preserve_insertion_order = false")
    con.execute(f"ATTACH '{MERCHANT_DB}' AS p (READ_ONLY)")
    con.execute(r"CREATE OR REPLACE MACRO digits(x) AS regexp_replace(COALESCE(CAST(x AS VARCHAR), ''), '[^0-9]', '', 'g')")
    con.execute(r"""
    CREATE OR REPLACE MACRO name_norm(n) AS
        trim(regexp_replace(replace(strip_accents(upper(COALESCE(n, ''))), 'Ł', 'L'), '[^A-Z0-9]+', ' ', 'g'))
    """)
    con.execute(f"CREATE OR REPLACE MACRO strip_tills(n) AS trim(regexp_replace(n, '{TILL_RE}', ''))")
    con.execute("""
    CREATE OR REPLACE MACRO hav_km(lat1, lon1, lat2, lon2) AS
        2 * 6371.0 * asin(sqrt(pow(sin(radians(lat2 - lat1) / 2), 2)
            + cos(radians(lat1)) * cos(radians(lat2)) * pow(sin(radians(lon2 - lon1) / 2), 2)))
    """)
    try:
        split = pd.read_csv(split_path, usecols=["store", "postcode", "split"], dtype=str)
        split["shop_pc"] = split["postcode"].str.replace(r"[^0-9]", "", regex=True)
        split.loc[split["shop_pc"].str.len() != 5, "shop_pc"] = pd.NA
        con.register("shops_df", split)
        con.execute("CREATE OR REPLACE TABLE shops AS SELECT * FROM shops_df")
        con.unregister("shops_df")

        _log("STAGE 3a restaurant cards")
        con.execute("""
        CREATE OR REPLACE TABLE merchant_key AS
        SELECT card, month, pc5, nm1, mcc, any_value(merchant) AS merchant
        FROM p.rest_cp
        WHERE cp = 1 AND mcc IN (5812, 5814) AND month BETWEEN 202501 AND 202606
        GROUP BY ALL
        """)
        con.execute(f"""
        CREATE OR REPLACE TABLE tx AS
        SELECT
            pymt_crd_acct_num_raw AS card,
            CAST(prch_mnth_id AS INTEGER) AS month,
            CAST(mrch_catg_cd AS INTEGER) AS mcc,
            CASE WHEN length(digits(mrch_postal_code)) = 5 THEN digits(mrch_postal_code) END AS pc5,
            strip_tills(name_norm(mrch_nm_raw)) AS nm1,
            SUM(CAST(cs_tran_amt AS DOUBLE)) AS spend,
            COUNT(*) AS txns,
            SUM(CAST(cs_tran_amt AS DOUBLE)) FILTER (WHERE weekend) AS weekend_spend,
            SUM(CAST(cs_tran_amt AS DOUBLE)) FILTER (WHERE dinner) AS dinner_spend,
            any_value(home_pc5) AS home_pc5,
            bool_or(is_foreign) AS is_foreign,
            bool_or(is_premium) AS is_premium,
            bool_or(is_business) AS is_business
        FROM (
            SELECT *,
                extract('dow' FROM try_cast(prch_dt AS DATE)) IN (0, 6) AS weekend,
                ((try_cast(substr(lpad(regexp_replace(CAST(tran_id_gmt_tm AS VARCHAR), '[^0-9]', '', 'g'), 6, '0'), 1, 2) AS INTEGER) + 2) % 24)
                    BETWEEN 17 AND 21 AS dinner,
                CASE WHEN length(digits(pstl_cd_enr)) = 5 THEN digits(pstl_cd_enr) END AS home_pc5,
                try_cast(issr_ctry_cd AS INTEGER) <> 616 AS is_foreign,
                upper(COALESCE(crd_typ_nm, '')) SIMILAR TO '.*(GOLD|PLATIN|INFINITE|SIGNATURE|WORLD|BLACK).*' AS is_premium,
                upper(COALESCE(prod_id_pltfrm_cd_vcis, '')) IN ('CO', 'BZ', 'GV') AS is_business
            FROM read_parquet('{parquet}')
            WHERE mrch_ctry_cd = 616
              AND transaction_type <> 'ATM'
              AND CAST(cp_flag AS INTEGER) = 1
              AND CAST(mrch_catg_cd AS INTEGER) IN (5812, 5814)
              AND CAST(prch_mnth_id AS INTEGER) BETWEEN 202501 AND 202606
        )
        GROUP BY ALL
        """)
        con.execute("""
        CREATE OR REPLACE TABLE shop_tx AS
        SELECT s.store, s.shop_pc, s.split, t.month, t.card, t.home_pc5,
               t.spend, t.txns, t.weekend_spend, t.dinner_spend,
               t.is_foreign, t.is_premium, t.is_business
        FROM tx t
        JOIN merchant_key k
          ON k.card = t.card AND k.month = t.month AND k.mcc = t.mcc AND k.nm1 = t.nm1
         AND k.pc5 IS NOT DISTINCT FROM t.pc5
        JOIN shops s ON s.store = k.merchant
        """)
        matched, keyed = con.execute("SELECT (SELECT COUNT(*) FROM shop_tx), (SELECT COUNT(*) FROM tx)").fetchone()
        _log(f"matched card-months {matched:,} of {keyed:,} ({100 * matched / keyed:.1f}%)  ({time.time() - t0:.0f}s)")
        con.execute("""
        CREATE OR REPLACE TABLE shop_comp AS
        SELECT store, any_value(shop_pc) AS shop_pc, any_value(split) AS split,
            SUM(spend) FILTER (WHERE month BETWEEN 202507 AND 202606) AS spend,
            SUM(txns) FILTER (WHERE month BETWEEN 202507 AND 202606) AS txns,
            COUNT(DISTINCT card) FILTER (WHERE month BETWEEN 202507 AND 202606) AS cards,
            SUM(spend) FILTER (WHERE month BETWEEN 202507 AND 202606 AND home_pc5 IS NOT DISTINCT FROM shop_pc) AS local_spend,
            SUM(spend) FILTER (WHERE month BETWEEN 202507 AND 202606 AND is_foreign) AS foreign_spend,
            SUM(spend) FILTER (WHERE month BETWEEN 202507 AND 202606 AND is_premium) AS premium_spend,
            SUM(spend) FILTER (WHERE month BETWEEN 202507 AND 202606 AND is_business) AS business_spend,
            SUM(weekend_spend) FILTER (WHERE month BETWEEN 202507 AND 202606) AS weekend_spend,
            SUM(dinner_spend) FILTER (WHERE month BETWEEN 202507 AND 202606) AS dinner_spend,
            SUM(spend) FILTER (WHERE month BETWEEN 202501 AND 202506) AS h1_2025,
            SUM(spend) FILTER (WHERE month BETWEEN 202601 AND 202606) AS h1_2026
        FROM shop_tx
        GROUP BY store
        """)
        n_shops = con.execute("SELECT COUNT(*) FROM shop_comp").fetchone()[0]
        _log(f"shops with card features {n_shops:,}")
        con.execute("""
        CREATE OR REPLACE TABLE home_wallet AS
        SELECT home_pc5, SUM(spend) AS spend, COUNT(DISTINCT card) AS cards
        FROM shop_tx
        WHERE split = 'train' AND month BETWEEN 202507 AND 202606 AND home_pc5 IS NOT NULL
        GROUP BY home_pc5
        """)
        con.execute("""
        CREATE OR REPLACE TABLE shop_home AS
        SELECT store, home_pc5, SUM(spend) AS spend
        FROM shop_tx
        WHERE split = 'train' AND month BETWEEN 202507 AND 202606 AND home_pc5 IS NOT NULL
        GROUP BY store, home_pc5
        """)
        con.execute("""
        CREATE OR REPLACE TABLE nearby AS
        SELECT a.pc5 AS shop_pc, b.pc5 AS home_pc5
        FROM p.pc_geo a
        JOIN p.pc_geo b
          ON abs(a.lat - b.lat) <= 0.0462
         AND abs(a.lon - b.lon) <= 0.0462 / cos(radians(a.lat))
         AND hav_km(a.lat, a.lon, b.lat, b.lon) <= 5
        """)
        con.execute("""
        CREATE OR REPLACE TABLE catchment_zip AS
        SELECT n.shop_pc, SUM(w.spend) AS spend, SUM(w.cards) AS cards
        FROM nearby n
        JOIN home_wallet w ON w.home_pc5 = n.home_pc5
        GROUP BY n.shop_pc
        """)
        con.execute("""
        CREATE OR REPLACE TABLE catchment_own AS
        SELECT s.store, SUM(sh.spend) AS own_spend
        FROM shops s
        JOIN nearby n ON n.shop_pc = s.shop_pc
        JOIN shop_home sh ON sh.store = s.store AND sh.home_pc5 = n.home_pc5
        WHERE s.split = 'train'
        GROUP BY s.store
        """)
        _log("STAGE 3b grocery and lodging scan")
        con.execute(f"""
        CREATE OR REPLACE TABLE other_zip AS
        SELECT
            CASE WHEN length(digits(mrch_postal_code)) = 5 THEN digits(mrch_postal_code) END AS pc5,
            SUM(CAST(cs_tran_amt AS DOUBLE)) FILTER (WHERE mcc = 5411) AS grocery_spend,
            SUM(CAST(cs_tran_amt AS DOUBLE)) FILTER (WHERE mcc IN (7011, 7012)) AS lodging_spend,
            SUM(CAST(cs_tran_amt AS DOUBLE)) FILTER (WHERE mcc IN (5813, 7832, 7922, 7941, 7991, 7996)) AS leisure_spend
        FROM (
            SELECT digits(mrch_postal_code) AS mrch_postal_code,
                   CAST(mrch_catg_cd AS INTEGER) AS mcc,
                   cs_tran_amt
            FROM read_parquet('{parquet}')
            WHERE mrch_ctry_cd = 616
              AND transaction_type <> 'ATM'
              AND CAST(cp_flag AS INTEGER) = 1
              AND CAST(prch_mnth_id AS INTEGER) BETWEEN 202507 AND 202606
              AND CAST(mrch_catg_cd AS INTEGER) IN (5411, 7011, 7012, 5813, 7832, 7922, 7941, 7991, 7996)
        )
        GROUP BY 1
        HAVING pc5 IS NOT NULL
        """)
        _log(f"card database ready  ({time.time() - t0:.0f}s)")
    finally:
        con.close()


def _duck(path: Path | None = None) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(path) if path else ":memory:")
    con.execute("SET memory_limit = '24GB'")
    con.execute("SET temp_directory = '/tmp/duckdb_revenue'")
    con.execute("SET preserve_insertion_order = false")
    con.execute(r"CREATE OR REPLACE MACRO digits(x) AS regexp_replace(COALESCE(CAST(x AS VARCHAR), ''), '[^0-9]', '', 'g')")
    con.execute(r"""
    CREATE OR REPLACE MACRO name_norm(n) AS
        trim(regexp_replace(replace(strip_accents(upper(COALESCE(n, ''))), 'Ł', 'L'), '[^A-Z0-9]+', ' ', 'g'))
    """)
    con.execute(f"CREATE OR REPLACE MACRO strip_tills(n) AS trim(regexp_replace(n, '{TILL_RE}', ''))")
    return con


def shops_from_raw(parquet: Path) -> pd.DataFrame:
    """One row per shop. Revenue is July 2025 through June 2026."""
    con = _duck()
    con.execute(f"ATTACH '{MERCHANT_DB}' AS p (READ_ONLY)")
    con.execute("""
    CREATE TABLE key_store AS
    SELECT nm1, city, pc5, month, any_value(merchant) AS merchant, bool_or(hub) AS hub
    FROM p.rest_cp
    WHERE mcc IN (5812, 5814)
    GROUP BY ALL
    """)
    con.execute(f"""
    CREATE TABLE raw_tx AS
    SELECT mrch_nm_raw,
           strip_tills(name_norm(mrch_nm_raw)) AS nm1,
           name_norm(mrch_city_nm_raw) AS city,
           CASE WHEN length(digits(mrch_postal_code)) = 5 THEN digits(mrch_postal_code) END AS pc5,
           CAST(prch_mnth_id AS INTEGER) AS month,
           CAST(mrch_catg_cd AS INTEGER) AS mcc,
           pymt_crd_acct_num_raw AS card,
           CAST(cs_tran_amt AS DOUBLE) AS amt
    FROM read_parquet('{parquet}')
    WHERE mrch_ctry_cd = 616
      AND transaction_type <> 'ATM'
      AND CAST(cp_flag AS INTEGER) = 1
      AND CAST(mrch_catg_cd AS INTEGER) IN (5812, 5814)
    """)
    con.execute("""
    CREATE TABLE raw_st AS
    SELECT r.*, k.merchant, k.hub
    FROM raw_tx r
    JOIN key_store k
      ON k.nm1 = r.nm1
     AND k.city IS NOT DISTINCT FROM r.city
     AND k.pc5 IS NOT DISTINCT FROM r.pc5
     AND k.month = r.month
    """)
    tills = con.execute("""
    WITH r AS (
        SELECT mrch_nm_raw, merchant,
               SUM(amt) FILTER (WHERE month BETWEEN 202501 AND 202512) AS revenue_2025,
               SUM(amt) FILTER (WHERE month BETWEEN 202507 AND 202512) AS revenue_h2_2025,
               SUM(amt) FILTER (WHERE month BETWEEN 202601 AND 202606) AS revenue_h1_2026,
               COUNT(DISTINCT card) FILTER (WHERE month BETWEEN 202501 AND 202512) AS cards_2025,
               COUNT(DISTINCT month) FILTER (WHERE month BETWEEN 202501 AND 202512) AS months_active_2025,
               arg_max(mcc, amt) FILTER (WHERE month BETWEEN 202501 AND 202512) AS mrch_catg_cd
        FROM raw_st
        GROUP BY ALL
    )
    SELECT r.mrch_nm_raw,
           r.merchant AS store,
           st.brand,
           st.pc5,
           st.powiat AS district,
           r.mrch_catg_cd,
           r.revenue_h2_2025,
           r.revenue_h1_2026,
           r.cards_2025,
           r.months_active_2025,
           r.months_active_2025 = 12 AS full_year_2025
    FROM r
    JOIN p.rows_train_2025 st ON st.merchant = r.merchant
    WHERE r.revenue_2025 > 0
    """).df()
    con.close()

    tills["year_revenue"] = tills["revenue_h2_2025"].fillna(0) + tills["revenue_h1_2026"].fillna(0)
    ordered = tills.sort_values(["store", "year_revenue"], ascending=[True, False])
    shops = ordered.groupby("store", as_index=False).first()
    shops["revenue"] = shops["store"].map(tills.groupby("store")["year_revenue"].sum())
    shops["cards"] = shops["store"].map(tills.groupby("store")["cards_2025"].sum())
    shops["months_active"] = shops["store"].map(tills.groupby("store")["months_active_2025"].max())
    shops["full_year"] = shops["store"].map(tills.groupby("store")["full_year_2025"].max())
    shops = shops[(shops["revenue"] > 0) & shops["pc5"].notna()].reset_index(drop=True)
    shops["postcode"] = shops["pc5"].str[:2] + "-" + shops["pc5"].str[2:]
    shops["brand"] = shops["brand"].fillna(shops["store"])
    n_shops_brand = shops.groupby("brand")["store"].transform("nunique")
    shops["brand_tier"] = np.where(n_shops_brand.eq(1), "small", np.where(n_shops_brand.lt(5), "medium", "big"))
    return shops


def demand_from_raw() -> pd.DataFrame:
    """One row per shop. A training shop is left out of its own postcode sums."""
    con = _duck(DEMAND_DB)
    n = con.execute("SELECT COUNT(*) FROM duckdb_tables() WHERE table_name = 'shop_tx'").fetchone()[0]
    if n == 0:
        raise FileNotFoundError(f"No card extract in {DEMAND_DB}.")
    comp = con.execute("SELECT * FROM shop_comp").df()
    catchment = con.execute("""
        SELECT s.store, z.spend AS catchment_spend, z.cards AS catchment_cards, COALESCE(o.own_spend, 0) AS own_nearby
        FROM shops s
        LEFT JOIN catchment_zip z ON z.shop_pc = s.shop_pc
        LEFT JOIN catchment_own o ON o.store = s.store
    """).df()
    other = con.execute("SELECT * FROM other_zip").df()
    wallet = con.execute("SELECT home_pc5 AS shop_pc, spend AS resident_spend FROM home_wallet").df()
    con.close()

    train = comp[comp["split"] == "train"].copy()
    sum_cols = [
        "spend", "txns", "cards", "local_spend", "foreign_spend", "premium_spend",
        "business_spend", "weekend_spend", "dinner_spend", "h1_2025", "h1_2026",
    ]
    for col in sum_cols:
        train[col] = train[col].fillna(0)
        comp[col] = comp[col].fillna(0)
    zip_sum = train.groupby("shop_pc")[sum_cols].sum().reset_index()
    base = comp.merge(zip_sum, on="shop_pc", how="left", suffixes=("", "_zip"))
    base = base.merge(wallet, on="shop_pc", how="left")
    base = base.merge(catchment, on="store", how="left")
    base = base.merge(other, left_on="shop_pc", right_on="pc5", how="left")

    def other_than_self(row_col: str, zip_col: str) -> pd.Series:
        own = np.where(base["split"].eq("train"), base[row_col], 0.0)
        return base[zip_col].fillna(0) - own

    spend = other_than_self("spend", "spend_zip")
    txns = other_than_self("txns", "txns_zip")
    cards = other_than_self("cards", "cards_zip")
    local = other_than_self("local_spend", "local_spend_zip")
    resident = base["resident_spend"].fillna(0) - np.where(base["split"].eq("train"), base["local_spend"], 0.0)
    outside = (resident - local).clip(lower=0)

    def share(part: pd.Series) -> pd.Series:
        return np.where(spend > 0, part / spend, np.nan)

    return pd.DataFrame({
        "store": base["store"],
        "leak_outside_share": np.where(resident > 0, outside / resident, np.nan),
        "local_share": share(local),
        "foreign_share": share(other_than_self("foreign_spend", "foreign_spend_zip")),
        "premium_share": share(other_than_self("premium_spend", "premium_spend_zip")),
        "business_share": share(other_than_self("business_spend", "business_spend_zip")),
        "weekend_share": share(other_than_self("weekend_spend", "weekend_spend_zip")),
        "dinner_share": share(other_than_self("dinner_spend", "dinner_spend_zip")),
        "avg_ticket": np.where(txns > 0, spend / txns, np.nan),
        "txns_per_card": np.where(cards > 0, txns / cards, np.nan),
        "spend_trend": np.where(
            other_than_self("h1_2025", "h1_2025_zip") > 0,
            other_than_self("h1_2026", "h1_2026_zip") / other_than_self("h1_2025", "h1_2025_zip"),
            np.nan,
        ),
        "catchment_cards": base["catchment_cards"],
        "catchment_spend": base["catchment_spend"].fillna(0) - np.where(base["split"].eq("train"), base["own_nearby"], 0.0),
        "grocery_spend": base["grocery_spend"],
        "lodging_spend": base["lodging_spend"],
    })


def cells_from_shops(shops: pd.DataFrame, demand: pd.DataFrame) -> pd.DataFrame:
    """One row per postcode and MCC. The target is the median full-year shop."""
    shop_pc = shops.merge(demand[["store"] + DEMAND_FEATURES + ["txns_per_card"]], on="store", how="left")
    zip_demand = shop_pc.groupby("pc5", as_index=False)[DEMAND_FEATURES].median()
    txns = shop_pc.groupby("pc5", as_index=False)["txns_per_card"].median()
    largest_brand = shop_pc.groupby(["pc5", "mrch_catg_cd", "brand"]).size().groupby(level=[0, 1]).max()
    cells = shop_pc.groupby(["pc5", "mrch_catg_cd"], as_index=False).agg(
        revenue=("revenue", "median"),
        n_shops=("store", "size"),
        n_brands=("brand", "nunique"),
        zip_cards=("cards", "sum"),
        share_big=("brand_tier", lambda s: (s == "big").mean()),
        district=("district", "first"),
        full_year_share=("full_year", "mean"),
        median_months_active=("months_active", "median"),
        n_full_year=("full_year", "sum"),
    )
    cells["n_competitor_shops"] = cells["n_shops"] - cells.set_index(["pc5", "mrch_catg_cd"]).index.map(largest_brand)
    full_year_median = (
        shop_pc[shop_pc["full_year"]]
        .groupby(["pc5", "mrch_catg_cd"], as_index=False)
        .agg(revenue_full_year=("revenue", "median"))
    )
    cells = cells.merge(zip_demand, on="pc5", how="left").merge(txns, on="pc5", how="left")
    cells = cells.merge(full_year_median, on=["pc5", "mrch_catg_cd"], how="left")
    cells["cards_per_shop"] = cells["zip_cards"] / cells["n_shops"]
    cells["spend_per_shop_proxy"] = cells["cards_per_shop"] * cells["txns_per_card"] * cells["avg_ticket"]
    zip_ids = cells["pc5"].drop_duplicates().sort_values().to_numpy()
    train_zips = set(np.random.default_rng(SEED).permutation(zip_ids)[: int(round(0.70 * len(zip_ids)))])
    cells["split"] = np.where(cells["pc5"].isin(train_zips), "train", "test")
    return cells


def metrics(y, pred_y, revenue, pred_revenue) -> dict:
    ape = np.abs(revenue - pred_revenue) / revenue
    varied = np.std(pred_y) > 0
    top_true = y >= np.quantile(y, 0.8)
    top_pred = pred_y >= np.quantile(pred_y, 0.8)
    return {
        "cells": len(y),
        "r2": float(r2_score(y, pred_y)),
        "mape": float(ape.mean()),
        "median_ape": float(np.median(ape)),
        "spearman": float(spearmanr(y, pred_y).statistic) if varied else np.nan,
        "top20_precision": float(top_true[top_pred].mean()) if varied else np.nan,
    }


def predict(cells: pd.DataFrame, revenue_col: str) -> tuple[dict, pd.DataFrame]:
    """XGBoost on log(revenue / MCC median). Returns held-out predictions."""
    work = cells.dropna(subset=[revenue_col]).copy()
    train = work[work["split"] == "train"].copy()
    test = work[work["split"] == "test"].copy()
    med = train.groupby("mrch_catg_cd")[revenue_col].median()
    y_train = np.log(train[revenue_col] / train["mrch_catg_cd"].map(med))
    y_test = np.log(test[revenue_col] / test["mrch_catg_cd"].map(med))
    imputer = SimpleImputer(strategy="median")
    x_train = imputer.fit_transform(train[FEATURES])
    x_test = imputer.transform(test[FEATURES])
    model = XGBRegressor(**XGB_PARAMS).fit(x_train, y_train)
    pred_y = model.predict(x_test)
    pred = np.exp(pred_y) * test["mrch_catg_cd"].map(med).to_numpy()
    scored = metrics(y_test.to_numpy(), pred_y, test[revenue_col].to_numpy(), pred)
    out = test[["pc5", "mrch_catg_cd", "district", "n_shops", "n_full_year", revenue_col]].copy()
    out = out.rename(columns={revenue_col: "actual_revenue"})
    out["predicted_revenue"] = pred
    out["ape"] = np.abs(out["actual_revenue"] - out["predicted_revenue"]) / out["actual_revenue"]
    return scored, out.sort_values(["mrch_catg_cd", "pc5"]).reset_index(drop=True)


def _fit(train: pd.DataFrame, revenue_col: str):
    med = train.groupby("mrch_catg_cd")[revenue_col].median()
    y = np.log(train[revenue_col] / train["mrch_catg_cd"].map(med))
    imputer = SimpleImputer(strategy="median").fit(train[FEATURES])
    model = XGBRegressor(**XGB_PARAMS).fit(imputer.transform(train[FEATURES]), y)

    def score(df: pd.DataFrame) -> np.ndarray:
        return np.exp(model.predict(imputer.transform(df[FEATURES]))) * df["mrch_catg_cd"].map(med).to_numpy()

    return score


def predict_all(cells: pd.DataFrame, revenue_col: str = "revenue_full_year") -> pd.DataFrame:
    """Every (postcode, MCC) cell. Cells the model trains on get out-of-fold predictions."""
    labelled = cells[(cells["n_full_year"] >= MIN_FULL_YEAR) & cells[revenue_col].notna()]
    out = cells.copy()
    out["predicted_revenue"] = _fit(labelled, revenue_col)(out)
    for tr, te in GroupKFold(n_splits=FOLDS).split(labelled, groups=labelled["pc5"]):
        out.loc[labelled.index[te], "predicted_revenue"] = _fit(labelled.iloc[tr], revenue_col)(labelled.iloc[te])
    out["out_of_fold"] = out.index.isin(labelled.index)
    return out


def evaluate(cells: pd.DataFrame) -> pd.DataFrame:
    """Scores on held-out postcodes. Writes metrics.csv and heldout_predictions.csv."""
    populations = [
        ("full-year shops, at least 5", cells[cells["n_full_year"] >= MIN_FULL_YEAR], "revenue_full_year"),
        ("all shops, at least 5", cells[cells["n_shops"] >= 5], "revenue"),
    ]
    rows = []
    for name, frame, revenue_col in populations:
        scored, predictions = predict(frame, revenue_col)
        rows.append({"population": name, **scored})
        if revenue_col == "revenue_full_year":
            predictions.to_csv(HERE / "heldout_predictions.csv", index=False)
    summary = pd.DataFrame(rows).set_index("population")
    summary.to_csv(HERE / "metrics.csv")
    print(summary.round(3).to_string())
    _log(f"Wrote {HERE / 'heldout_predictions.csv'}")
    _log(f"Wrote {HERE / 'metrics.csv'}")
    return summary


def zip_revenue(cells: pd.DataFrame) -> pd.DataFrame:
    """One row per postcode and restaurant type. Writes zip_revenue.csv."""
    pred = predict_all(cells)
    oof = pred[pred["out_of_fold"]]
    ape = (oof["revenue_full_year"] - oof["predicted_revenue"]).abs() / oof["revenue_full_year"]
    _log(f"Out-of-fold cells {len(oof):,}: MAPE {ape.mean():.3f}, median APE {ape.median():.3f}")
    _log(f"Scored by the full model (fewer than {MIN_FULL_YEAR} full-year shops): {(~pred['out_of_fold']).sum():,}")
    result = pd.DataFrame({
        "zip_code": pred["pc5"].str[:2] + "-" + pred["pc5"].str[2:],
        "type": pred["mrch_catg_cd"].map(TYPE),
        "predicted_revenue": pred["predicted_revenue"].round(2),
    }).sort_values(["zip_code", "type"]).reset_index(drop=True)
    result.to_csv(ZIP_REVENUE, index=False)
    _log(f"Wrote {ZIP_REVENUE}: {len(result):,} rows, {result['zip_code'].nunique():,} postcodes")
    return result


def run(rebuild: bool = False) -> pd.DataFrame:
    if not GEONAMES.is_file():
        raise FileNotFoundError(GEONAMES)
    if rebuild:
        for db in (MERCHANT_DB, DEMAND_DB):
            db.unlink(missing_ok=True)
            db.with_name(db.name + ".wal").unlink(missing_ok=True)
    parquet = find_parquet()
    build_merchant_db(parquet)
    _log(f"STAGE 2/4 shop table. Reading {parquet.name}")
    shops = shops_from_raw(parquet)
    build_demand_db(parquet, shops)
    _log(f"Shops: {len(shops):,}")
    demand = demand_from_raw()
    _log(f"Demand rows: {len(demand):,}")
    cells = cells_from_shops(shops, demand)
    _log("STAGE 4/4 evaluate on held-out postcodes, then predict every postcode")
    evaluate(cells)
    return zip_revenue(cells)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="delete pl_rest.duckdb and zip_demand.duckdb and rebuild them from the parquet",
    )
    run(rebuild=parser.parse_args().rebuild)
