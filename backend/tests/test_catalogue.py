import pytest

from backend.app.ml.catalogue import CatalogueEvidence


@pytest.fixture
def catalogue():
    return CatalogueEvidence([
        {"cth": "85411000", "description": "Diodes, other than photosensitive or light-emitting diodes (LED)", "heading_text": "Semiconductor devices"},
        {"cth": "85414100", "description": "Light-emitting diodes (LED)", "heading_text": "Semiconductor devices"},
        {"cth": "85312000", "description": "Indicator panels incorporating liquid crystal devices (LCD) or light-emitting diodes (LED)", "heading_text": "Electric signalling apparatus"},
        {"cth": "85395100", "description": "Light-emitting diode modules", "heading_text": "Electric light sources"},
        {"cth": "95067000", "description": "Ice skates and roller skates, including skating boots with skates attached", "heading_text": "Articles and equipment for sports"},
        {"cth": "85362010", "description": "Air circuit breakers", "heading_text": "Electrical apparatus (for example, switches, connectors, circuit breakers) for a voltage not exceeding 1,000 volts"},
        {"cth": "84879000", "description": "Other", "heading_text": "Machinery parts, not containing electrical connectors, contacts or other electrical features"},
    ])


def test_excluded_component_is_not_positive_evidence(catalogue):
    assert catalogue.inspect("LIGHT EMITTING DIODE")['accepted'] == "85414100"
    assert catalogue.inspect("roller skates shoes size 32")['accepted'] == "95067000"


def test_full_article_wins_over_component_prefix(catalogue):
    assert catalogue.inspect("light emitting diode module")['accepted'] == "85395100"
    assert catalogue.inspect("connector for light emitting diodes")['accepted'] == ""
    assert catalogue.inspect("packaging for roller skates")['accepted'] == ""
    assert catalogue.inspect("roller skates for a doll")['accepted'] == ""
    assert catalogue.inspect("light emitting diodes with a driver board")['accepted'] == ""


@pytest.mark.parametrize("description", ["not light emitting diodes", "light emitting diodes or roller skates", "light emitting diodes and roller skates"])
def test_negated_or_mixed_goods_require_review(catalogue, description):
    assert catalogue.inspect(description)['accepted'] == ""


def test_inherited_voltage_condition_is_required(catalogue):
    assert catalogue.inspect("air circuit breakers")['accepted'] == ""
    assert catalogue.inspect("air circuit breakers 240V")['accepted'] == "85362010"
    assert catalogue.inspect("air circuit breakers 33kV")['accepted'] == ""


def test_inherited_subheading_specifications_are_not_dropped():
    catalogue = CatalogueEvidence([{"cth": "01010101", "description": "Example product",
                                    "heading_text": "Example goods", "subheading_text": "Weighing less than 10 kg"}])
    assert catalogue.inspect('example product')['accepted'] == ''


def test_negative_heading_terms_do_not_classify_as_machinery_parts(catalogue):
    evidence = catalogue.inspect("2 pole SMD connector -CONT0002")
    assert evidence['accepted'] == ""
    assert evidence['headings'] == ['8536']


def test_sourced_terminology_supports_heading_not_leaf(catalogue):
    evidence = catalogue.inspect("connection and contact elements for wires and cables")
    assert evidence['prefix'] == '8536'
    assert evidence['accepted'] == ''
    assert evidence['source'].startswith('https://new.cbic.gov.in/')


def test_collision_between_specific_leaf_names_is_not_accepted():
    evidence = CatalogueEvidence([
        {"cth": "01010101", "description": "Example product", "heading_text": "First family"},
        {"cth": "02020202", "description": "Example product", "heading_text": "Second family"},
    ])
    assert evidence.inspect('example product')['accepted'] == ''
