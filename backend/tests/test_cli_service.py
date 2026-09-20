"""Tests for cli/service.py: the device authorization grant (RFC 8628) Plak
brokers itself, against a real PostgreSQL (testcontainers).

These tests really commit (single-use rows, session rotation, deletes) and
therefore use sessions of their own, like test_ingest_service.py, instead of
the rollback fixture from conftest.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from plak.cli import service as cli
from plak.models.cli import CliDeviceAuthorization, CliDeviceStatus, CliRefreshToken, CliSession
from plak.models.identity import Member, MemberStatus

NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def factory(migrated_dsn: str):
    engine = create_async_engine(migrated_dsn)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _make_member(factory, *, status: MemberStatus = MemberStatus.ACTIVE, sub: str | None = None) -> Member:
    async with factory() as db:
        member = Member(sso_subject=sub or f"sub-{uuid.uuid4().hex}", email=f"{uuid.uuid4().hex}@example.nl",
                        status=status)
        db.add(member)
        await db.commit()
        return member


async def _create(factory, *, client_name: str | None = None, now: datetime = NOW) -> cli.NewDeviceAuthorization:
    async with factory() as db:
        return await cli.create_device_authorization(db, client_name=client_name, ip_truncated="203.0.113.0/24",
                                                      now=now)


async def _approve(factory, created: cli.NewDeviceAuthorization, member: Member, now: datetime = NOW):
    async with factory() as db:
        return await cli.decide(db, created.user_code, member, approve=True, now=now)


async def _deny(factory, created: cli.NewDeviceAuthorization, member: Member, now: datetime = NOW):
    async with factory() as db:
        return await cli.decide(db, created.user_code, member, approve=False, now=now)


async def _exchange(factory, device_code: str, *, now: datetime = NOW) -> cli.IssuedTokens:
    async with factory() as db:
        return await cli.exchange_device_code(db, device_code, now=now)


async def _refresh(factory, refresh_token: str, *, now: datetime = NOW) -> cli.IssuedTokens:
    async with factory() as db:
        return await cli.refresh(db, refresh_token, now=now)


async def _get_authorization(factory, id_: uuid.UUID) -> CliDeviceAuthorization | None:
    async with factory() as db:
        return await db.get(CliDeviceAuthorization, id_)


async def _get_session(factory, id_: uuid.UUID) -> CliSession | None:
    async with factory() as db:
        return await db.get(CliSession, id_)


async def _refresh_token_row(factory, session_id: uuid.UUID) -> list[CliRefreshToken]:
    async with factory() as db:
        rows = await db.scalars(select(CliRefreshToken).where(CliRefreshToken.session_id == session_id))
        return list(rows)


# -- Pure helpers -------------------------------------------------------------


class TestNormaliseUserCode:
    def test_accepts_lowercase_no_hyphen(self):
        assert cli.normalise_user_code("wdjbmjht") == "WDJBMJHT"

    def test_accepts_hyphen(self):
        assert cli.normalise_user_code("WDJB-MJHT") == "WDJBMJHT"

    def test_accepts_spaces(self):
        assert cli.normalise_user_code("WDJB MJHT") == "WDJBMJHT"

    def test_accepts_mixed_case_with_tab(self):
        assert cli.normalise_user_code("wdjb\tmjht") == "WDJBMJHT"

    def test_rejects_wrong_length(self):
        assert cli.normalise_user_code("WDJB-MJH") is None

    def test_rejects_excluded_characters(self):
        # 0, O, 1, I are not in the alphabet.
        assert cli.normalise_user_code("WDJB-MJH0") is None

    def test_format_user_code_inserts_hyphen(self):
        assert cli.format_user_code("WDJBMJHT") == "WDJB-MJHT"


class TestSanitiseClientName:
    def test_none_stays_none(self):
        assert cli.sanitise_client_name(None) is None

    def test_empty_becomes_none(self):
        assert cli.sanitise_client_name("   ") is None

    def test_strips_control_characters(self):
        assert cli.sanitise_client_name("plak-cli\x00 1.2") == "plak-cli 1.2"

    def test_strips_bidi_override(self):
        assert cli.sanitise_client_name("plak-cli‮ evil") == "plak-cli evil"

    def test_newlines_become_spaces(self):
        assert cli.sanitise_client_name("plak-cli\n1.2\r\nmacOS") == "plak-cli 1.2 macOS"

    def test_collapses_whitespace(self):
        assert cli.sanitise_client_name("plak-cli    1.2") == "plak-cli 1.2"

    def test_truncates_to_max_length(self):
        long_name = "a" * 150
        result = cli.sanitise_client_name(long_name)
        assert result == "a" * cli.CLIENT_NAME_MAX_LENGTH

    def test_truncation_can_yield_empty(self):
        # Truncating to 100 removes nothing here; verify the boundary itself
        # (all whitespace after collapsing) still returns None.
        assert cli.sanitise_client_name("\t\n  \r") is None


class TestParseSelector:
    def test_valid(self):
        value, selector = "plakdc", "a" * 16
        plaintext = f"{value}_{selector}_{'b' * 64}"
        assert cli.parse_selector(plaintext, "plakdc") == selector

    def test_wrong_prefix(self):
        plaintext = f"plakcli_{'a' * 16}_{'b' * 64}"
        assert cli.parse_selector(plaintext, "plakdc") is None

    def test_wrong_part_count(self):
        assert cli.parse_selector("plakdc_onlyoneunderscore", "plakdc") is None

    def test_wrong_selector_length(self):
        plaintext = f"plakdc_{'a' * 15}_{'b' * 64}"
        assert cli.parse_selector(plaintext, "plakdc") is None

    def test_wrong_secret_length(self):
        plaintext = f"plakdc_{'a' * 16}_{'b' * 63}"
        assert cli.parse_selector(plaintext, "plakdc") is None

    def test_non_hex_characters(self):
        plaintext = f"plakdc_{'g' * 16}_{'b' * 64}"
        assert cli.parse_selector(plaintext, "plakdc") is None


class TestMatches:
    def test_correct_secret(self):
        stored = cli.hash_secret("geheim")
        assert cli.matches("geheim", stored) is True

    def test_wrong_secret(self):
        stored = cli.hash_secret("geheim")
        assert cli.matches("verkeerd", stored) is False

    def test_unknown_selector_no_stored_hash(self):
        # Costs the same as a wrong secret (dummy hash), but must still be False.
        assert cli.matches("iets", None) is False


# -- create_device_authorization ---------------------------------------------


class TestCreateDeviceAuthorization:
    async def test_creates_pending_row_with_expected_shape(self, factory):
        created = await _create(factory, client_name="plak-cli 1.2")
        assert created.authorization.status == CliDeviceStatus.PENDING
        assert len(created.user_code) == cli.USER_CODE_LENGTH
        assert all(char in cli.USER_CODE_ALPHABET for char in created.user_code)
        assert created.device_code.startswith(cli.DEVICE_CODE_PREFIX + "_")
        assert created.authorization.expires_at == NOW + cli.DEVICE_CODE_TTL
        assert created.authorization.client_name == "plak-cli 1.2"
        # Neither secret is ever stored in the clear.
        assert created.device_code not in created.authorization.device_hash
        row = await _get_authorization(factory, created.authorization.id)
        assert row.device_hash == cli.hash_secret(created.device_code)
        assert row.user_code_hash == cli.hash_secret(created.user_code)

    async def test_defaults_to_real_time_without_now(self, factory):
        before = datetime.now(UTC)
        async with factory() as db:
            created = await cli.create_device_authorization(db, client_name=None, ip_truncated=None)
        after = datetime.now(UTC)
        assert before <= created.authorization.created_at <= after

    async def test_sweeps_expired_rows_opportunistically(self, factory):
        stale = await _create(factory, now=NOW - cli.DEVICE_CODE_TTL - timedelta(seconds=1))
        await _create(factory, now=NOW)
        assert await _get_authorization(factory, stale.authorization.id) is None


# -- pending_by_user_code ------------------------------------------------------


class TestPendingByUserCode:
    async def test_finds_pending(self, factory):
        created = await _create(factory)
        async with factory() as db:
            found = await cli.pending_by_user_code(db, created.user_code, now=NOW)
        assert found is not None
        assert found.id == created.authorization.id

    async def test_none_for_malformed_code(self, factory):
        async with factory() as db:
            assert await cli.pending_by_user_code(db, "not-a-code!!", now=NOW) is None

    async def test_none_for_unknown_code(self, factory):
        async with factory() as db:
            assert await cli.pending_by_user_code(db, "AAAA-AAAA", now=NOW) is None

    async def test_none_once_expired(self, factory):
        created = await _create(factory)
        async with factory() as db:
            found = await cli.pending_by_user_code(
                db, created.user_code, now=NOW + cli.DEVICE_CODE_TTL + timedelta(seconds=1)
            )
        assert found is None

    async def test_none_once_decided(self, factory):
        created = await _create(factory)
        member = await _make_member(factory)
        await _approve(factory, created, member)
        async with factory() as db:
            assert await cli.pending_by_user_code(db, created.user_code, now=NOW) is None

    async def test_for_update_flag_still_finds_row(self, factory):
        created = await _create(factory)
        async with factory() as db:
            found = await cli.pending_by_user_code(db, created.user_code, now=NOW, for_update=True)
            assert found is not None
            await db.rollback()


# -- decide (approve/deny) ----------------------------------------------------


class TestDecide:
    async def test_approve_sets_status_and_member(self, factory):
        created = await _create(factory)
        member = await _make_member(factory)
        result = await _approve(factory, created, member)
        assert result is not None
        assert result.status == CliDeviceStatus.APPROVED
        assert result.member_id == member.id
        assert result.decided_at == NOW

    async def test_deny_sets_status_without_member_requirement(self, factory):
        created = await _create(factory)
        member = await _make_member(factory)
        result = await _deny(factory, created, member)
        assert result is not None
        assert result.status == CliDeviceStatus.DENIED
        assert result.decided_at == NOW

    async def test_unknown_user_code_returns_none(self, factory):
        member = await _make_member(factory)
        async with factory() as db:
            result = await cli.decide(db, "AAAA-AAAA", member, approve=True, now=NOW)
        assert result is None

    async def test_already_decided_returns_none(self, factory):
        created = await _create(factory)
        member = await _make_member(factory)
        await _approve(factory, created, member)
        async with factory() as db:
            result = await cli.decide(db, created.user_code, member, approve=True, now=NOW)
        assert result is None


# -- exchange_device_code ------------------------------------------------------


class TestExchangeDeviceCode:
    async def test_malformed_device_code_is_invalid_grant(self, factory):
        with pytest.raises(cli.GrantError) as excinfo:
            await _exchange(factory, "not-a-device-code")
        assert excinfo.value.code == cli.INVALID_GRANT

    async def test_unknown_selector_is_invalid_grant(self, factory):
        unknown = f"plakdc_{'a' * 16}_{'b' * 64}"
        with pytest.raises(cli.GrantError) as excinfo:
            await _exchange(factory, unknown)
        assert excinfo.value.code == cli.INVALID_GRANT

    async def test_wrong_secret_is_invalid_grant(self, factory):
        created = await _create(factory)
        selector = cli.parse_selector(created.device_code, cli.DEVICE_CODE_PREFIX)
        tampered = f"{cli.DEVICE_CODE_PREFIX}_{selector}_{'0' * 64}"
        with pytest.raises(cli.GrantError) as excinfo:
            await _exchange(factory, tampered)
        assert excinfo.value.code == cli.INVALID_GRANT

    async def test_expired_device_code(self, factory):
        created = await _create(factory)
        with pytest.raises(cli.GrantError) as excinfo:
            await _exchange(factory, created.device_code, now=NOW + cli.DEVICE_CODE_TTL + timedelta(seconds=1))
        assert excinfo.value.code == cli.EXPIRED_TOKEN

    async def test_pending_is_authorization_pending(self, factory):
        created = await _create(factory)
        with pytest.raises(cli.GrantError) as excinfo:
            await _exchange(factory, created.device_code)
        assert excinfo.value.code == cli.AUTHORIZATION_PENDING

    async def test_polling_too_fast_is_slow_down(self, factory):
        created = await _create(factory)
        # First poll establishes last_polled_at.
        with pytest.raises(cli.GrantError):
            await _exchange(factory, created.device_code, now=NOW)
        # A second poll less than (interval - tolerance) seconds later.
        too_soon = NOW + timedelta(seconds=cli.POLL_INTERVAL_S - cli.POLL_TOLERANCE_S - 1)
        with pytest.raises(cli.GrantError) as excinfo:
            await _exchange(factory, created.device_code, now=too_soon)
        assert excinfo.value.code == cli.SLOW_DOWN

    async def test_polling_within_tolerance_is_not_slow_down(self, factory):
        created = await _create(factory)
        with pytest.raises(cli.GrantError):
            await _exchange(factory, created.device_code, now=NOW)
        # Exactly (interval - tolerance) seconds later must NOT trigger SLOW_DOWN.
        on_time = NOW + timedelta(seconds=cli.POLL_INTERVAL_S - cli.POLL_TOLERANCE_S)
        with pytest.raises(cli.GrantError) as excinfo:
            await _exchange(factory, created.device_code, now=on_time)
        assert excinfo.value.code == cli.AUTHORIZATION_PENDING

    async def test_denied_is_access_denied(self, factory):
        created = await _create(factory)
        member = await _make_member(factory)
        await _deny(factory, created, member)
        with pytest.raises(cli.GrantError) as excinfo:
            await _exchange(factory, created.device_code)
        assert excinfo.value.code == cli.ACCESS_DENIED

    async def test_approving_member_no_longer_active_is_access_denied_and_deletes_row(self, factory):
        created = await _create(factory)
        member = await _make_member(factory)
        await _approve(factory, created, member)
        async with factory() as db:
            db_member = await db.get(Member, member.id)
            db_member.status = MemberStatus.DEACTIVATED
            await db.commit()
        with pytest.raises(cli.GrantError) as excinfo:
            await _exchange(factory, created.device_code)
        assert excinfo.value.code == cli.ACCESS_DENIED
        assert await _get_authorization(factory, created.authorization.id) is None

    async def test_success_issues_tokens_and_deletes_authorization(self, factory):
        created = await _create(factory)
        member = await _make_member(factory)
        await _approve(factory, created, member)
        issued = await _exchange(factory, created.device_code)
        assert issued.member.id == member.id
        assert issued.access_token.startswith(cli.ACCESS_TOKEN_PREFIX + "_")
        assert issued.refresh_token.startswith(cli.REFRESH_TOKEN_PREFIX + "_")
        assert issued.session.access_expires_at == NOW + cli.ACCESS_TOKEN_TTL
        assert issued.session.expires_at == NOW + cli.REFRESH_IDLE_TTL
        assert issued.session.max_expires_at == NOW + cli.SESSION_MAX_TTL
        assert await _get_authorization(factory, created.authorization.id) is None

    async def test_exchange_is_single_use(self, factory):
        created = await _create(factory)
        member = await _make_member(factory)
        await _approve(factory, created, member)
        await _exchange(factory, created.device_code)
        with pytest.raises(cli.GrantError) as excinfo:
            await _exchange(factory, created.device_code)
        assert excinfo.value.code == cli.INVALID_GRANT


# -- refresh --------------------------------------------------------------


async def _issue_session(factory, *, member: Member | None = None, now: datetime = NOW) -> cli.IssuedTokens:
    member = member or await _make_member(factory)
    created = await _create(factory, now=now)
    await _approve(factory, created, member, now=now)
    return await _exchange(factory, created.device_code, now=now)


class TestRefresh:
    async def test_malformed_token_is_invalid_grant(self, factory):
        with pytest.raises(cli.GrantError) as excinfo:
            await _refresh(factory, "not-a-refresh-token")
        assert excinfo.value.code == cli.INVALID_GRANT

    async def test_unknown_selector_is_invalid_grant(self, factory):
        unknown = f"plakclr_{'a' * 16}_{'b' * 64}"
        with pytest.raises(cli.GrantError) as excinfo:
            await _refresh(factory, unknown)
        assert excinfo.value.code == cli.INVALID_GRANT

    async def test_wrong_secret_is_invalid_grant(self, factory):
        issued = await _issue_session(factory)
        selector = cli.parse_selector(issued.refresh_token, cli.REFRESH_TOKEN_PREFIX)
        tampered = f"{cli.REFRESH_TOKEN_PREFIX}_{selector}_{'0' * 64}"
        with pytest.raises(cli.GrantError) as excinfo:
            await _refresh(factory, tampered)
        assert excinfo.value.code == cli.INVALID_GRANT

    async def test_success_rotates_tokens(self, factory):
        issued = await _issue_session(factory)
        later = NOW + timedelta(hours=1)
        rotated = await _refresh(factory, issued.refresh_token, now=later)
        assert rotated.refresh_token != issued.refresh_token
        assert rotated.access_token != issued.access_token
        assert rotated.session.id == issued.session.id
        assert rotated.session.last_used_at == later
        assert rotated.session.expires_at == later + cli.REFRESH_IDLE_TTL

    async def test_success_caps_expiry_at_hard_max(self, factory):
        # Idle expiry is 30 days, so approaching the 90-day hard cap needs a
        # chain of refreshes, each within the previous idle window.
        issued = await _issue_session(factory)
        first = await _refresh(factory, issued.refresh_token, now=NOW + timedelta(days=20))
        second = await _refresh(factory, first.refresh_token, now=NOW + timedelta(days=45))
        third = await _refresh(factory, second.refresh_token, now=NOW + timedelta(days=70))
        assert third.session.expires_at == third.session.max_expires_at == NOW + cli.SESSION_MAX_TTL

    async def test_reuse_of_used_token_deletes_session_and_raises(self, factory):
        member = await _make_member(factory)
        issued = await _issue_session(factory, member=member)
        later = NOW + timedelta(hours=1)
        rotated = await _refresh(factory, issued.refresh_token, now=later)
        # The original refresh token was rotated away; using it again after
        # the grace window is reuse.
        with pytest.raises(cli.RefreshReuseError) as excinfo:
            await _refresh(factory, issued.refresh_token, now=later + cli.REFRESH_REUSE_GRACE)
        assert excinfo.value.member_sub == member.sso_subject
        assert excinfo.value.code == cli.INVALID_GRANT
        assert await _get_session(factory, issued.session.id) is None
        # The rotated (still-valid) token is gone too, since the session is gone.
        with pytest.raises(cli.GrantError):
            await _refresh(factory, rotated.refresh_token, now=later + cli.REFRESH_REUSE_GRACE)

    async def test_reuse_within_the_grace_window_is_refused_but_keeps_the_session(self, factory):
        issued = await _issue_session(factory)
        later = NOW + timedelta(hours=1)
        rotated = await _refresh(factory, issued.refresh_token, now=later)
        with pytest.raises(cli.GrantError) as excinfo:
            await _refresh(factory, issued.refresh_token, now=later + timedelta(seconds=9))
        assert not isinstance(excinfo.value, cli.RefreshReuseError)
        assert excinfo.value.code == cli.INVALID_GRANT
        assert await _get_session(factory, issued.session.id) is not None
        # The winner's token keeps working.
        again = await _refresh(factory, rotated.refresh_token, now=later + timedelta(seconds=10))
        assert again.session.id == issued.session.id

    async def test_the_grace_covers_only_the_immediately_previous_token(self, factory):
        issued = await _issue_session(factory)
        later = NOW + timedelta(hours=1)
        second = await _refresh(factory, issued.refresh_token, now=later)
        await _refresh(factory, second.refresh_token, now=later + timedelta(seconds=1))
        # The first token is two rotations old: reuse, even within ten seconds.
        with pytest.raises(cli.RefreshReuseError):
            await _refresh(factory, issued.refresh_token, now=later + timedelta(seconds=2))
        assert await _get_session(factory, issued.session.id) is None

    async def test_parallel_refreshes_with_one_token_leave_one_winner(self, factory):
        issued = await _issue_session(factory)
        later = NOW + timedelta(hours=1)
        results = await asyncio.gather(
            _refresh(factory, issued.refresh_token, now=later),
            _refresh(factory, issued.refresh_token, now=later),
            return_exceptions=True,
        )
        winners = [result for result in results if isinstance(result, cli.IssuedTokens)]
        losers = [result for result in results if isinstance(result, cli.GrantError)]
        assert len(winners) == 1
        assert len(losers) == 1
        assert not isinstance(losers[0], cli.RefreshReuseError)
        assert await _get_session(factory, issued.session.id) is not None

    async def test_idle_expiry_deletes_session(self, factory):
        issued = await _issue_session(factory)
        past_idle = NOW + cli.REFRESH_IDLE_TTL + timedelta(seconds=1)
        with pytest.raises(cli.GrantError) as excinfo:
            await _refresh(factory, issued.refresh_token, now=past_idle)
        assert excinfo.value.code == cli.INVALID_GRANT
        assert await _get_session(factory, issued.session.id) is None

    async def test_hard_max_expiry_deletes_session(self, factory):
        issued = await _issue_session(factory)
        past_max = NOW + cli.SESSION_MAX_TTL + timedelta(seconds=1)
        with pytest.raises(cli.GrantError) as excinfo:
            await _refresh(factory, issued.refresh_token, now=past_max)
        assert excinfo.value.code == cli.INVALID_GRANT
        assert await _get_session(factory, issued.session.id) is None

    async def test_inactive_member_is_refused_without_deleting(self, factory):
        member = await _make_member(factory)
        issued = await _issue_session(factory, member=member)
        async with factory() as db:
            db_member = await db.get(Member, member.id)
            db_member.status = MemberStatus.DEACTIVATED
            await db.commit()
        later = NOW + timedelta(hours=1)
        with pytest.raises(cli.GrantError) as excinfo:
            await _refresh(factory, issued.refresh_token, now=later)
        assert excinfo.value.code == cli.INVALID_GRANT
        # Unlike expiry/reuse, an inactive member does not cost the session.
        assert await _get_session(factory, issued.session.id) is not None


# -- session_for_access_token ---------------------------------------------


class TestSessionForAccessToken:
    async def test_unknown_selector_returns_none(self, factory):
        unknown = f"plakcli_{'a' * 16}_{'b' * 64}"
        async with factory() as db:
            assert await cli.session_for_access_token(db, unknown, now=NOW) is None

    async def test_malformed_returns_none(self, factory):
        async with factory() as db:
            assert await cli.session_for_access_token(db, "garbage", now=NOW) is None

    async def test_wrong_secret_returns_none(self, factory):
        issued = await _issue_session(factory)
        selector = cli.parse_selector(issued.access_token, cli.ACCESS_TOKEN_PREFIX)
        tampered = f"{cli.ACCESS_TOKEN_PREFIX}_{selector}_{'0' * 64}"
        async with factory() as db:
            assert await cli.session_for_access_token(db, tampered, now=NOW) is None

    async def test_valid_returns_session(self, factory):
        issued = await _issue_session(factory)
        async with factory() as db:
            found = await cli.session_for_access_token(db, issued.access_token, now=NOW)
        assert found is not None
        assert found.id == issued.session.id

    async def test_access_expired_after_one_hour_returns_none(self, factory):
        issued = await _issue_session(factory)
        past_access = NOW + cli.ACCESS_TOKEN_TTL + timedelta(seconds=1)
        async with factory() as db:
            assert await cli.session_for_access_token(db, issued.access_token, now=past_access) is None

    async def test_session_expired_returns_none(self, factory):
        issued = await _issue_session(factory)
        async with factory() as db:
            session = await db.get(CliSession, issued.session.id)
            session.expires_at = NOW - timedelta(seconds=1)
            await db.commit()
        async with factory() as db:
            assert await cli.session_for_access_token(db, issued.access_token, now=NOW) is None

    async def test_max_expired_returns_none(self, factory):
        issued = await _issue_session(factory)
        async with factory() as db:
            session = await db.get(CliSession, issued.session.id)
            session.max_expires_at = NOW - timedelta(seconds=1)
            await db.commit()
        async with factory() as db:
            assert await cli.session_for_access_token(db, issued.access_token, now=NOW) is None


# -- mark_used ------------------------------------------------------------


class TestMarkUsed:
    async def test_sets_last_used_at(self, factory):
        issued = await _issue_session(factory)
        later = NOW + timedelta(minutes=5)
        async with factory() as db:
            await cli.mark_used(db, issued.session.id, now=later)
        row = await _get_session(factory, issued.session.id)
        assert row.last_used_at == later


# -- revoke -----------------------------------------------------------------


class TestRevoke:
    async def test_revokes_without_member_filter(self, factory):
        issued = await _issue_session(factory)
        async with factory() as db:
            revoked = await cli.revoke(db, issued.session.id)
        assert revoked is True
        assert await _get_session(factory, issued.session.id) is None

    async def test_revokes_with_matching_member_filter(self, factory):
        member = await _make_member(factory)
        issued = await _issue_session(factory, member=member)
        async with factory() as db:
            revoked = await cli.revoke(db, issued.session.id, member_id=member.id)
        assert revoked is True
        assert await _get_session(factory, issued.session.id) is None

    async def test_refuses_with_wrong_member_filter(self, factory):
        member = await _make_member(factory)
        other = await _make_member(factory)
        issued = await _issue_session(factory, member=member)
        async with factory() as db:
            revoked = await cli.revoke(db, issued.session.id, member_id=other.id)
        assert revoked is False
        assert await _get_session(factory, issued.session.id) is not None

    async def test_unknown_session_returns_false(self, factory):
        async with factory() as db:
            assert await cli.revoke(db, uuid.uuid4()) is False


# -- sessions_of --------------------------------------------------------------


class TestSessionsOf:
    async def test_lists_only_unexpired_newest_first(self, factory):
        member = await _make_member(factory)
        older = await _issue_session(factory, member=member, now=NOW)
        newer = await _issue_session(factory, member=member, now=NOW + timedelta(minutes=1))
        expired = await _issue_session(factory, member=member, now=NOW + timedelta(minutes=2))
        async with factory() as db:
            session = await db.get(CliSession, expired.session.id)
            session.expires_at = NOW - timedelta(seconds=1)
            await db.commit()
        async with factory() as db:
            rows = await cli.sessions_of(db, member.id, now=NOW + timedelta(minutes=5))
        assert [row.id for row in rows] == [newer.session.id, older.session.id]

    async def test_excludes_other_members(self, factory):
        member = await _make_member(factory)
        other = await _make_member(factory)
        await _issue_session(factory, member=other)
        async with factory() as db:
            rows = await cli.sessions_of(db, member.id, now=NOW)
        assert rows == []


# -- delete_expired -----------------------------------------------------------


class TestDeleteExpired:
    async def test_sweeps_both_kinds_and_returns_counts(self, factory):
        fresh_authorization = await _create(factory, now=NOW)
        member = await _make_member(factory)
        idle_expired_session = await _issue_session(factory, member=member, now=NOW)
        async with factory() as db:
            session = await db.get(CliSession, idle_expired_session.session.id)
            session.expires_at = NOW - timedelta(seconds=1)
            await db.commit()
        fresh_session = await _issue_session(factory, member=member, now=NOW)

        # Inserted directly, and only after every create_device_authorization
        # call above, so no other call's own opportunistic sweep removes this
        # row before delete_expired gets to it.
        stale_id = uuid.uuid4()
        async with factory() as db:
            db.add(
                CliDeviceAuthorization(
                    id=stale_id,
                    device_selector=uuid.uuid4().hex[:16],
                    device_hash=cli.hash_secret("irrelevant"),
                    user_code_hash=cli.hash_secret("STALECODE"),
                    status=CliDeviceStatus.PENDING,
                    created_at=NOW - cli.DEVICE_CODE_TTL - timedelta(seconds=1),
                    expires_at=NOW - timedelta(seconds=1),
                )
            )
            await db.commit()

        async with factory() as db:
            counts = await cli.delete_expired(db, NOW)
        assert counts == (1, 1)
        assert await _get_authorization(factory, stale_id) is None
        assert await _get_authorization(factory, fresh_authorization.authorization.id) is not None
        assert await _get_session(factory, idle_expired_session.session.id) is None
        assert await _get_session(factory, fresh_session.session.id) is not None


class TestLogoutLookupAndRevokeAll:
    async def test_an_expired_but_genuine_access_token_still_names_its_session(self, factory):
        issued = await _issue_session(factory)
        async with factory() as db:
            await db.execute(
                update(CliSession).values(access_expires_at=NOW - timedelta(hours=1))
            )
            await db.commit()
        async with factory() as db:
            session = await cli.session_for_logout(db, access_token=issued.access_token)
        assert session.id == issued.session.id

    async def test_a_wrong_secret_names_nothing(self, factory):
        issued = await _issue_session(factory)
        selector = issued.access_token.split("_")[1]
        async with factory() as db:
            assert await cli.session_for_logout(db, access_token=f"plakcli_{selector}_{'0' * 64}") is None
            assert await cli.session_for_logout(db, access_token="rommel") is None
            assert await cli.session_for_logout(db, refresh_token=f"plakclr_{'a' * 16}_{'b' * 64}") is None
            assert await cli.session_for_logout(db) is None

    async def test_a_current_or_rotated_refresh_token_names_its_session(self, factory):
        issued = await _issue_session(factory)
        rotated = await _refresh(factory, issued.refresh_token, now=NOW + timedelta(minutes=1))
        async with factory() as db:
            by_old = await cli.session_for_logout(db, refresh_token=issued.refresh_token)
            by_new = await cli.session_for_logout(db, refresh_token=rotated.refresh_token)
        assert by_old.id == by_new.id == issued.session.id

    async def test_an_unknown_access_token_falls_through_to_the_refresh_token(self, factory):
        issued = await _issue_session(factory)
        async with factory() as db:
            session = await cli.session_for_logout(
                db, access_token="plakcli_" + "a" * 16 + "_" + "b" * 64, refresh_token=issued.refresh_token
            )
        assert session.id == issued.session.id

    async def test_revoke_all_of_removes_only_that_members_sessions(self, factory):
        member = await _make_member(factory)
        mine = [await _issue_session(factory, member=member) for _ in range(2)]
        other = await _issue_session(factory)
        async with factory() as db:
            assert await cli.revoke_all_of(db, member.id) == 2
            await db.commit()
        for issued in mine:
            assert await _get_session(factory, issued.session.id) is None
        assert await _get_session(factory, other.session.id) is not None
