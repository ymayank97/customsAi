"""Build a starter precedent corpus of Indian customs classification rulings.

Stage 4 of the new pipeline narrows a contested Bill-of-Entry row to a pair of
competing tariff headings (e.g. ``8536`` vs ``8544``, ``9032`` vs ``8537``).
This script writes a small, structured corpus of *real* CAAR / CESTAT / Supreme
Court / CBEC classification rulings plus a lexical TF-IDF index keyed on the
contested headings, so the runtime ``precedent`` service can answer
"is there a ruling on point?" without any live web fetching.

Every ruling embedded below was retrieved and verified from a primary or
reputable secondary source (URL recorded per row). Re-running this script
rebuilds the parquet + index deterministically from the embedded data:

    .venv/Scripts/python.exe -m backend.app.scripts.build_precedent_corpus

Outputs (relative to repo root):
    data/precedent/rulings.parquet
    data/precedent/precedent_index.pkl
"""
from __future__ import annotations

import pathlib
import pickle
import re

import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer

from backend.app.core.text_cleaning import clean_description, normalize_for_matching

OUT_DIR = pathlib.Path("data/precedent")


# ---------------------------------------------------------------------------
# Verified rulings.
#
# Each dict:
#   ruling_id      stable slug
#   title          short human title
#   authority      tribunal/authority + bench
#   date           ruling/decision date (ISO where known)
#   headings       list of 4-digit headings in play
#   cth_codes      list of 8-digit tariff items where stated
#   competing_pair "AAAA|BBBB" sorted 4-digit pair the dispute turned on
#   outcome_cth    the heading/item the authority held correct
#   legal_basis    GIR rule / section/chapter note / circular / HSN EN cited
#   summary        2-4 sentence factual summary
#   source_url     URL the ruling was verified from
# ---------------------------------------------------------------------------
RULINGS: list[dict] = [
    {
        "ruling_id": "valeo-vcu-pcu-2025",
        "title": "Valeo India — EV Vehicle/Powertrain Control Units (VCU/PCU)",
        "authority": "CAAR Mumbai",
        "date": "2025-04-19",
        "headings": ["8537", "8543", "9032", "8708", "8714"],
        "cth_codes": ["87089900", "87141090"],
        "competing_pair": "8537|8708",
        "outcome_cth": "87089900",
        "legal_basis": "Section XVII notes + GIR; rejected 8537/8543/9032 as the units are dedicated EV parts",
        "summary": (
            "Valeo sought classification of Vehicle Control Units / Powertrain "
            "Control Units that manage motor drive, battery management and "
            "communication in electric 2/3/4-wheelers. CAAR rejected the proposed "
            "8537, 8543 and 9032 headings and held the units are parts of motor "
            "vehicles: CTI 87089900 for 3/4-wheelers and 87141090 for 2-wheelers."
        ),
        "source_url": "https://taxguru.in/custom-duty/caar-ruling-vcu-pcu-classification-evs.html",
    },
    {
        "ruling_id": "mobis-bcm-ibu-2025",
        "title": "Mobis India — Body Control Module / Integrated Body Unit",
        "authority": "CESTAT Chennai",
        "date": "2025-08-08",
        "headings": ["9032", "8537"],
        "cth_codes": ["90328910", "85371000"],
        "competing_pair": "8537|9032",
        "outcome_cth": "90328910",
        "legal_basis": "Chapter 90 Note 7 + Board Circular 09.05.1997 (Programmable Process Controller vs PLC)",
        "summary": (
            "Mobis imported automotive Body Control Modules and Integrated Body "
            "Units, declared under CTH 90328910 at 0% BCD; the department sought "
            "CTH 85371000 at 5%. CESTAT upheld 9032, relying on Board Circular "
            "09.05.1997 distinguishing Programmable Process Controllers (9032) "
            "from Programmable Logic Controllers (8537), the goods performing "
            "automatic regulation without human intervention."
        ),
        "source_url": "https://aadrikaalaw.com/wp-content/uploads/2025/08/alo-document-10-2.pdf",
    },
    {
        "ruling_id": "kafila-forge-universal-joints-8483",
        "title": "Kafila Forge — Universal Joint cross parts (transmission shaft)",
        "authority": "CESTAT Delhi",
        "date": "2019-11-01",
        "headings": ["8483", "8708"],
        "cth_codes": ["84836090"],
        "competing_pair": "8483|8708",
        "outcome_cth": "84836090",
        "legal_basis": "Section XVII Note 2 (parts more specifically provided elsewhere); specificity over residual 8708",
        "summary": (
            "Kafila Forge imported Universal Joint cross parts/cups for "
            "transmission shafts, classified under CTH 84836090; the department "
            "sought motor-vehicle-parts heading 8708. CESTAT held the goods are "
            "more specifically covered by 84836090 and excluded from 8708 by "
            "Section XVII Note 2."
        ),
        "source_url": "https://taxguru.in/custom-duty/heading-8483-60-90-covers-universal-joints-parts-transmission-shaft.html",
    },
    {
        "ruling_id": "gs-auto-international-2003-sc",
        "title": "G.S. Auto International — spring bolts/nuts for motor vehicles",
        "authority": "Supreme Court of India",
        "date": "2003-01-01",
        "headings": ["8708", "7318"],
        "cth_codes": [],
        "competing_pair": "7318|8708",
        "outcome_cth": "8708",
        "legal_basis": "Section XVII; 'solely or principally with motor vehicles' sole-use test",
        "summary": (
            "The Supreme Court laid down the test for heading 8708: goods "
            "suitable for use solely or principally with motor vehicles of "
            "8701-8705 are classifiable as motor-vehicle parts under 8708, "
            "subject to Section XVII Note 2 (parts of general use). On the facts, "
            "spring bolts with nuts made for motor vehicles fell under 8708."
        ),
        "source_url": "https://gstdoctor.com/articles/what-is-the-gst-rate-for-parts-of-automobiles-luv5zbkv",
    },
    {
        "ruling_id": "uniproducts-car-matting-2020-sc",
        "title": "CCE v. Uniproducts — car matting (textile floor covering)",
        "authority": "Supreme Court of India",
        "date": "2020-01-01",
        "headings": ["5703", "8708"],
        "cth_codes": [],
        "competing_pair": "5703|8708",
        "outcome_cth": "5703",
        "legal_basis": "HSN Explanatory Notes to Section XVII; specific textile entry 5703 prevails over 8708",
        "summary": (
            "The dispute was whether tufted textile car matting is a motor "
            "vehicle accessory of 8708 or a textile floor covering of 5703. The "
            "Supreme Court applied the HSN Explanatory Notes to Section XVII and "
            "held the goods fall under the specific entry 5703, not the general "
            "motor-vehicle-parts heading."
        ),
        "source_url": "https://www.lkslaw.com/insights/articles/continuing-dilemma-over-classification-of-parts-and-accessories-of-motor-vehicles/",
    },
    {
        "ruling_id": "eps-ecu-power-steering-8708-2024",
        "title": "EPS-ECU — electric power-steering electronic control unit",
        "authority": "CESTAT Delhi",
        "date": "2024-08-01",
        "headings": ["8708", "8537"],
        "cth_codes": ["87089400"],
        "competing_pair": "8537|8708",
        "outcome_cth": "87089400",
        "legal_basis": "EPS-ECU not a board/panel of 8537; sole-use automotive part under 8708",
        "summary": (
            "An Electric Power Steering ECU (and its sub-assembly) used solely in "
            "automobiles with power steering was held not to be an electrical "
            "board/panel of CTI 85371000. CESTAT Delhi classified it as a motor "
            "vehicle part under CTI 87089400 on the sole/principal-use test."
        ),
        "source_url": "https://www.livelaw.in/tax-cases/cestat-delhi-eps-ecu-automobiles-power-steering-cti-8708-94-00-electrical-machines-equipment-265288",
    },
    {
        "ruling_id": "huawei-ppcba-dwdm-8517-2023",
        "title": "Huawei — Populated PCB Assembly for DWDM (PSS 1830)",
        "authority": "CESTAT Mumbai",
        "date": "2023-10-05",
        "headings": ["8517"],
        "cth_codes": ["85177010", "85176290"],
        "competing_pair": "85176290|85177010",
        "outcome_cth": "85177010",
        "legal_basis": "Section XVI Note 2 (parts); HSN EN to 8517; goods are PCBs/parts not complete apparatus",
        "summary": (
            "Huawei imported Populated Printed Circuit Board Assemblies used in "
            "PSS 1830 DWDM equipment, declared under CTI 85177010 (parts); the "
            "department sought 85176290 (complete transmission apparatus). CESTAT "
            "held the cards are parts of DWDM equipment, not standalone apparatus, "
            "and confirmed 85177010, setting aside the reclassification."
        ),
        "source_url": "https://cestat.gov.in/weborders/file/mumbai/259092",
    },
    {
        "ruling_id": "bharti-airtel-router-parts-8517-2026",
        "title": "Bharti Airtel — Juniper router line/interface cards",
        "authority": "CESTAT Delhi",
        "date": "2026-01-10",
        "headings": ["8517"],
        "cth_codes": ["85177090", "85176290"],
        "competing_pair": "85176290|85177090",
        "outcome_cth": "85177090",
        "legal_basis": "Imported cards are parts of router, not Network Interface Cards; parts heading 85177090",
        "summary": (
            "Bharti Airtel imported modular port concentrators, line cards, "
            "interface cards and switch fabric boards for Juniper routers. The "
            "department sought CTI 85176290; CESTAT held they are parts of the "
            "router (not standalone Network Interface Cards) classifiable under "
            "CTI 85177090."
        ),
        "source_url": "https://taxguru.in/custom-duty/cestat-delhi-quashes-customs-duty-demand-bharti-airtel-router-parts-classification-dispute.html",
    },
    {
        "ruling_id": "vodafone-idea-matrix-cards-8517-2024",
        "title": "Vodafone Idea — Hybrid/Pure Matrix Cards for PTN equipment",
        "authority": "CESTAT Delhi",
        "date": "2024-07-02",
        "headings": ["8517"],
        "cth_codes": ["85177010", "85177090"],
        "competing_pair": "85177010|85177090",
        "outcome_cth": "85177010",
        "legal_basis": "Matrix cards are parts of PTN goods of 8517; CTI 85177010 (SFPs fall under 85177090)",
        "summary": (
            "Vodafone Idea imported Hybrid/Pure Matrix Cards for Packet "
            "Transport Network equipment. CESTAT classified the matrix cards "
            "under CTI 85177010 as parts of PTN goods (with Small Form Factor "
            "Pluggables falling under 85177090), quashing the duty demand."
        ),
        "source_url": "https://www.livelaw.in/tax-cases/hybrid-pure-matrix-cards-ptn-equipment-cestat-customs-duty-vodafone-idea-261989",
    },
    {
        "ruling_id": "hitevision-interactive-flat-panel-8471-2026",
        "title": "Hitevision Tech — interactive flat panel with inbuilt CPU",
        "authority": "CESTAT Chennai",
        "date": "2026-01-28",
        "headings": ["8471", "8473", "8528"],
        "cth_codes": [],
        "competing_pair": "8471|8528",
        "outcome_cth": "8471",
        "legal_basis": "Revenue did not discharge burden; ADP machine of 8471, parts under 8473",
        "summary": (
            "Interactive flat panels with an inbuilt CPU (declared 'AIO-VDU') "
            "were classified by CESTAT Chennai under CTH 8471 as automatic data "
            "processing machines, with their parts under 8473. The revenue's "
            "attempt to reclassify as monitors/display apparatus failed for want "
            "of evidence."
        ),
        "source_url": "https://taxguru.in/custom-duty/interactive-flat-panel-classified-cth-8471-parts-cth-8473.html",
    },
    {
        "ruling_id": "apple-rear-camera-module-8517-2025",
        "title": "Apple — Rear Camera Integrated Module Subassembly",
        "authority": "CAAR Delhi",
        "date": "2025-01-29",
        "headings": ["8517", "8525"],
        "cth_codes": ["85177990", "85258900"],
        "competing_pair": "85177990|85258900",
        "outcome_cth": "85177990",
        "legal_basis": "Module has no independent function; part of mobile phone of 8517 (Section XVI Note 2)",
        "summary": (
            "Apple sought classification of a rear camera integrated module "
            "subassembly for mobile phones. CAAR Delhi held the module, lacking "
            "independent function and integrated into the phone, is a part of a "
            "telephone of CTI 85177990 rather than a standalone television "
            "camera of 85258900."
        ),
        "source_url": "https://taxguru.in/custom-duty/rear-camera-integrated-module-subassembly-falls-cth-85177990-caar-delhi.html",
    },
    {
        "ruling_id": "nokia-sfp-transceiver-8517-2024",
        "title": "Nokia Solutions — SFP optical transceiver",
        "authority": "CAAR Delhi",
        "date": "2024-10-14",
        "headings": ["8517", "8544", "9013"],
        "cth_codes": ["85176290"],
        "competing_pair": "8517|8544",
        "outcome_cth": "85176290",
        "legal_basis": "Transceiver is transmission/reception apparatus of 8517, not a connector (8544) or LC device (9013)",
        "summary": (
            "Nokia sought classification of Small Form-factor Pluggable optical "
            "transceivers that encode/decode data into light pulses for fibre "
            "transmission. CAAR examined 8517 vs 8544 vs 9013 and treated the "
            "device by its primary transmission/reception function under heading "
            "8517 (CTI 85176290)."
        ),
        "source_url": "https://taxguru.in/custom-duty/caar-ruling-sfp-classification-nokia-solutions.html",
    },
    {
        "ruling_id": "cisco-sfp-transceiver-8517-2024",
        "title": "Cisco Commerce — SFP transceiver",
        "authority": "CAAR Mumbai",
        "date": "2024-12-29",
        "headings": ["8517", "8544", "9013"],
        "cth_codes": ["85176290"],
        "competing_pair": "8517|8544",
        "outcome_cth": "85176290",
        "legal_basis": "Transceiver combining transmission and reception is apparatus of heading 8517",
        "summary": (
            "Cisco sought classification of Small Form Factor Pluggable "
            "transceivers used in switches/routers. CAAR Mumbai considered 8517 "
            "vs 8544 vs 9013 and the duty exemption under Notification 57/2017, "
            "treating the transceiver as transmission/reception apparatus of "
            "heading 8517 (CTI 85176290)."
        ),
        "source_url": "https://taxguru.in/custom-duty/classification-sfp-transceivers-customs-ruling.html",
    },
    {
        "ruling_id": "ingram-micro-dc-switch-router-8517-2022",
        "title": "Ingram Micro — Data Centre Switch Router models",
        "authority": "CAAR Mumbai",
        "date": "2022-08-17",
        "headings": ["8517"],
        "cth_codes": ["85176290"],
        "competing_pair": "85176290|85177090",
        "outcome_cth": "85176290",
        "legal_basis": "Switch routers are transmission/reception apparatus of 8517 (CTI 85176290)",
        "summary": (
            "Ingram Micro sought classification of 15 Data Centre Switch Router "
            "models and applicability of Notifications 24/2005 and 50/2017. CAAR "
            "Mumbai classified the switch routers under CTI 85176290 as "
            "apparatus for communication in a wired/wireless network."
        ),
        "source_url": "https://taxguru.in/custom-duty/data-centre-switch-router-models-lassifiable-sub-heading-85176290.html",
    },
    {
        "ruling_id": "sanmina-power-modules-8504-2025",
        "title": "Sanmina-SCI — AC-DC and DC-DC converter power modules",
        "authority": "CAAR Mumbai",
        "date": "2025-12-15",
        "headings": ["8504", "8537", "8543"],
        "cth_codes": ["8504"],
        "competing_pair": "8504|8537",
        "outcome_cth": "8504",
        "legal_basis": "Static converters classified by function under heading 8504, not 8537/8543",
        "summary": (
            "Sanmina sought classification of AC-DC and DC-DC converter power "
            "modules used in Wi-Fi units, data-centre switches and uplink cards. "
            "CAAR Mumbai held the modules are static converters of heading 8504 "
            "rather than control panels (8537) or other machines (8543)."
        ),
        "source_url": "https://taxguru.in/custom-duty/ac-dc-dc-dc-power-modules-fall-heading-8504-caar-mumbai.html",
    },
    {
        "ruling_id": "mitsubishi-inverter-assembly-8504-2025",
        "title": "Mitsubishi Electric — automotive Inverter Assembly Units",
        "authority": "CAAR Delhi",
        "date": "2025-08-12",
        "headings": ["8504", "8503", "8708"],
        "cth_codes": ["85044090"],
        "competing_pair": "8504|8708",
        "outcome_cth": "85044090",
        "legal_basis": "AC/DC static converter function places goods in 8504, not motor parts (8503/8708)",
        "summary": (
            "Mitsubishi Electric sought classification of Inverter Assembly Units "
            "for mild-hybrid electric vehicles. CAAR Delhi held the units are "
            "static converters of CTI 85044090 by their AC/DC conversion "
            "function, not parts of motors or motor vehicles."
        ),
        "source_url": "https://taxguru.in/custom-duty/automotive-inverter-assembly-units-classifiable-8504-40-90-caar-delhi.html",
    },
    {
        "ruling_id": "denso-inverter-assembly-8504-2024",
        "title": "Denso Haryana — Inverter Assembly used in motor vehicles",
        "authority": "CAAR Delhi",
        "date": "2024-06-12",
        "headings": ["8504", "8708"],
        "cth_codes": ["85044010"],
        "competing_pair": "8504|8708",
        "outcome_cth": "85044010",
        "legal_basis": "Goods are static converters (AC↔DC) of heading 8504",
        "summary": (
            "Denso Haryana sought classification of an inverter assembly used in "
            "motor vehicles. CAAR Delhi held the device, which converts AC to DC "
            "and vice versa, is a static converter classifiable under CTI "
            "85044010 in heading 8504."
        ),
        "source_url": "https://taxguru.in/custom-duty/denso-haryana-private-limited-caar-delhi.html",
    },
    {
        "ruling_id": "secure-meters-lcd-9013-2015-sc",
        "title": "Secure Meters — Liquid Crystal Displays (LCDs)",
        "authority": "Supreme Court of India",
        "date": "2015-01-01",
        "headings": ["9013", "8531", "9028"],
        "cth_codes": [],
        "competing_pair": "8531|9013",
        "outcome_cth": "9013",
        "legal_basis": "LCD is a liquid crystal device of 9013.80; specific heading prevails over parts/signalling",
        "summary": (
            "Secure Meters imported LCDs used in electricity meters; the dispute "
            "was whether they are liquid crystal devices of 9013.80 or "
            "signalling apparatus/meter parts of 8531/9028. The Supreme Court "
            "held LCDs are covered by 'other devices' of 9013.80 and classified "
            "them there."
        ),
        "source_url": "https://www.indianbarassociation.org/supreme-court-judgment-ms-secure-meters-ltd-vs-commissioner-of-customs-new-delhi/",
    },
    {
        "ruling_id": "lever-combination-switch-cbec-circular-2004",
        "title": "Lever Combination Switch — CBEC Circular 51/2004 classification",
        "authority": "CBEC Circular 51/2004-Cus",
        "date": "2004-10-01",
        "headings": ["8537", "8536"],
        "cth_codes": [],
        "competing_pair": "8536|8537",
        "outcome_cth": "8537",
        "legal_basis": "Steering-column lever combination switch is a board/assembly of 8537 (not a bare switch of 8536)",
        "summary": (
            "A CBEC Tariff Conference addressed Lever Combination Switches "
            "(steering-column assemblies with mounted PCBs, switches and cables "
            "controlling indicators/wipers/headlights). The Board accepted that, "
            "though essentially of a switching nature, the assembly is "
            "classifiable under CTH 8537 rather than as a bare switch of 8536, "
            "while still qualifying for switch-parts duty concession."
        ),
        "source_url": "https://taxguru.in/custom-duty/assessment-automobile-parts-lever-combination-switch-declared-switch.html",
    },
    {
        "ruling_id": "universal-impex-roasted-areca-2008-2023",
        "title": "Universal Impex — Roasted Areca (Betel) Nuts",
        "authority": "CAAR Mumbai",
        "date": "2023-05-12",
        "headings": ["2008", "0802"],
        "cth_codes": ["20081920", "08028090"],
        "competing_pair": "0802|2008",
        "outcome_cth": "20081920",
        "legal_basis": "HSN EN to 2008 (dry-roasted areca nuts); specific 'other roasted nuts & seeds' entry",
        "summary": (
            "Universal Impex sought classification of roasted areca/betel nuts. "
            "CAAR Mumbai held that roasting takes the goods out of Chapter 8 "
            "(raw nuts of 0802) and into the specific CTI 20081920 ('other "
            "roasted nuts and seeds') of Chapter 20, following the HSN "
            "Explanatory Notes to heading 2008."
        ),
        "source_url": "https://taxguru.in/wp-content/uploads/2023/05/In-re-Universal-Impex-CAAR-Mumbai.pdf",
    },
    {
        "ruling_id": "madhan-agro-coconut-oil-2018-sc",
        "title": "CCE v. Madhan Agro — small-pack coconut oil",
        "authority": "Supreme Court of India",
        "date": "2018-04-13",
        "headings": ["1513", "3305"],
        "cth_codes": ["15131900", "33059019"],
        "competing_pair": "1513|3305",
        "outcome_cth": "3305",
        "legal_basis": "GIR Rule 3(c) + chapter notes; small retail packs marketed as hair oil fall under 3305",
        "summary": (
            "The dispute was whether coconut oil in small retail packs is edible "
            "oil of 1513 or hair oil of 3305. The Supreme Court applied GIR Rule "
            "3(c) and held that coconut oil in packings suitable for use as hair "
            "oil is classifiable under Chapter 3305, while bulk packs remain "
            "under 1513."
        ),
        "source_url": "https://api.sci.gov.in/supremecourt//2009/2009__Judgement_13-Apr-2018.pdf",
    },
]


_FOURDIGIT_RE = re.compile(r"\b(\d{4})\b")


def _doc_text(r: dict) -> str:
    """Build the lexical document for a ruling: cleaned title+summary + raw codes."""
    base = " ".join([
        normalize_for_matching(r.get("title", "")),
        normalize_for_matching(r.get("summary", "")),
        normalize_for_matching(r.get("legal_basis", "")),
    ])
    # Append codes/headings as bare tokens (cleaner strips long digit runs, so
    # add them explicitly for exact code/heading recall).
    codes = list(r.get("headings", [])) + list(r.get("cth_codes", []))
    if r.get("outcome_cth"):
        codes.append(str(r["outcome_cth"]))
    codes_txt = " ".join(str(c) for c in codes)
    return f"{base} {codes_txt}".strip()


def build(out_dir: pathlib.Path = OUT_DIR) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)

    df = pd.DataFrame(RULINGS, columns=[
        "ruling_id", "title", "authority", "date", "headings", "cth_codes",
        "competing_pair", "outcome_cth", "legal_basis", "summary", "source_url",
    ])
    if df["ruling_id"].duplicated().any():
        dups = df.loc[df["ruling_id"].duplicated(), "ruling_id"].tolist()
        raise ValueError(f"duplicate ruling_id(s): {dups}")

    df.to_parquet(out_dir / "rulings.parquet", index=False)

    docs = [_doc_text(r) for r in RULINGS]
    vectorizer = TfidfVectorizer(
        ngram_range=(1, 2),
        lowercase=True,
        min_df=1,
        token_pattern=r"(?u)\b\w+\b",
    )
    matrix = vectorizer.fit_transform(docs)

    index = {
        "version": 1,
        "vectorizer": vectorizer,
        "matrix": matrix,
        "ruling_ids": df["ruling_id"].tolist(),
        # lightweight per-row metadata for the runtime helper (avoids reloading parquet)
        "rows": df.to_dict(orient="records"),
    }
    with open(out_dir / "precedent_index.pkl", "wb") as fh:
        pickle.dump(index, fh)

    authorities = sorted(df["authority"].unique().tolist())
    pairs = sorted(df["competing_pair"].unique().tolist())
    return {
        "rulings": int(len(df)),
        "authorities": authorities,
        "competing_pairs": pairs,
        "parquet": str(out_dir / "rulings.parquet"),
        "index": str(out_dir / "precedent_index.pkl"),
    }


if __name__ == "__main__":
    import json

    stats = build()
    print(json.dumps(stats, indent=2))
