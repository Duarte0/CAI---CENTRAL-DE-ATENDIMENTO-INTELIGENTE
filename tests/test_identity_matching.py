from src.core.identity_resolution import (
    AcessoriasMatchInput,
    DigiSacMatchInput,
    is_brazilian_mobile_variant,
    match_identity,
)


def directory_contact(
    company_id: int,
    contact_id: int,
    *,
    mobile: str | None = None,
    email: str | None = None,
) -> AcessoriasMatchInput:
    return AcessoriasMatchInput(
        contact_id=contact_id,
        company_id=company_id,
        normalized_mobile=mobile,
        normalized_email=email,
        contact_is_present=True,
        contact_is_active=True,
        company_is_present=True,
        company_is_active=True,
    )


def test_exact_phone_collapses_same_company_but_preserves_each_evidence_row():
    matches = match_identity(
        DigiSacMatchInput(1, "551198765432", None, False),
        [
            directory_contact(10, 100, mobile="551198765432"),
            directory_contact(10, 101, mobile="551198765432"),
        ],
    )

    assert [match.evidence_type for match in matches] == [
        "exact_phone",
        "exact_phone",
    ]
    assert {match.acessorias_company_id for match in matches} == {10}
    assert len({match.acessorias_contact_id for match in matches}) == 2


def test_shared_exact_phone_is_ambiguous_by_distinct_company():
    matches = match_identity(
        DigiSacMatchInput(1, "551198765432", None, False),
        [
            directory_contact(10, 100, mobile="551198765432"),
            directory_contact(20, 200, mobile="551198765432"),
        ],
    )

    assert {match.acessorias_company_id for match in matches} == {10, 20}


def test_exact_email_is_supported_without_name_or_alias_matching():
    matches = match_identity(
        DigiSacMatchInput(1, None, "user@example.test", False),
        [directory_contact(10, 100, email="user@example.test")],
    )

    assert len(matches) == 1
    assert matches[0].evidence_type == "exact_email"


def test_valid_brazilian_mobile_variant_is_the_only_non_exact_phone_rule():
    assert is_brazilian_mobile_variant("551198765432", "5511998765432")
    matches = match_identity(
        DigiSacMatchInput(1, "551198765432", None, False),
        [directory_contact(10, 100, mobile="5511998765432")],
    )
    assert [match.evidence_type for match in matches] == ["brazil_mobile_variant"]


def test_invalid_variant_different_ddd_foreign_and_extra_change_are_rejected():
    for other in (
        "550098765432",
        "5521998765432",
        "4411998765432",
        "5511998765439",
        "55119987654321",
    ):
        assert not is_brazilian_mobile_variant("551198765432", other)


def test_groups_and_inactive_directory_rows_never_match():
    group_matches = match_identity(
        DigiSacMatchInput(1, "551198765432", "group@example.test", True),
        [directory_contact(10, 100, mobile="551198765432", email="group@example.test")],
    )
    inactive_matches = match_identity(
        DigiSacMatchInput(2, "551198765432", None, False),
        [
            AcessoriasMatchInput(
                101,
                10,
                "551198765432",
                None,
                True,
                True,
                True,
                False,
            )
        ],
    )

    assert group_matches == ()
    assert inactive_matches == ()
